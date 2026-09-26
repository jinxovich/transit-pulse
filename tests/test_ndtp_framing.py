"""Потоковый декодер NDTP: частичные и склеенные кадры, мусор, битый CRC."""

from dataclasses import replace

from transit_core.ndtp import FrameDecoder, NavCell, encode_handshake, encode_realtime

NAV = NavCell(timestamp=1_767_690_000, lon=37.6, lat=55.75, valid=True, speed_kmh=30)


def _stream(n: int = 3) -> list[bytes]:
    return [encode_handshake(10, 1)] + [
        encode_realtime(10, i + 2, replace(NAV, timestamp=NAV.timestamp + i)) for i in range(n)
    ]


def test_glued_frames_in_one_chunk():
    frames = _stream()
    dec = FrameDecoder()
    got = dec.feed(b"".join(frames))
    assert [f.raw for f in got] == frames
    assert (dec.crc_errors, dec.parse_errors, dec.resyncs, dec.pending) == (0, 0, 0, 0)


def test_byte_by_byte_partial_frames():
    frames = _stream()
    dec = FrameDecoder()
    got = [f for byte in b"".join(frames) for f in dec.feed(bytes([byte]))]
    assert [f.raw for f in got] == frames
    assert dec.resyncs == 0


def test_garbage_between_frames_is_skipped_with_resync():
    a, b, *_ = _stream()
    dec = FrameDecoder()
    got = dec.feed(b"\x01\x02\x7e" + a + b"garbage\x7e" + b)
    assert [f.raw for f in got] == [a, b]
    assert dec.resyncs >= 2
    assert dec.parse_errors == 0


def test_false_signature_with_bad_header_resyncs():
    a = _stream(0)[0]
    fake = b"\x7e\x7e\x02\x00" + b"\x00" * 11  # dataSize=2 < NPH
    dec = FrameDecoder()
    assert [f.raw for f in dec.feed(fake + a)] == [a]
    assert dec.resyncs >= 1


def test_oversized_data_size_resyncs():
    a = _stream(0)[0]
    fake = b"\x7e\x7e\xff\xff" + b"\x00" * 4 + b"\x02" + b"\x00" * 6
    dec = FrameDecoder(max_data_size=4096)
    assert [f.raw for f in dec.feed(fake + a)] == [a]
    assert dec.resyncs >= 1


def test_bad_crc_is_counted_but_frame_still_delivered():
    raw = bytearray(encode_realtime(10, 2, NAV))
    raw[6] ^= 0xFF
    dec = FrameDecoder()
    (frame,) = dec.feed(bytes(raw))
    assert not frame.crc_ok
    assert frame.nav == replace(NAV, flags=0b1110_0000)
    assert dec.crc_errors == 1


def test_parse_error_is_counted_and_stream_continues():
    good = encode_realtime(10, 3, NAV)
    bad = bytearray(encode_handshake(10, 1))
    bad[2] -= 2  # dataSize меньше: тело handshake 16 байт вместо 18
    dec = FrameDecoder()
    got = dec.feed(bytes(bad[:-2]) + good)
    assert [f.raw for f in got] == [good]
    assert dec.parse_errors == 1


def test_trailing_partial_signature_is_kept():
    a = _stream(0)[0]
    dec = FrameDecoder()
    assert dec.feed(b"junk" + a[:1]) == []
    assert [f.raw for f in dec.feed(a[1:])] == [a]
