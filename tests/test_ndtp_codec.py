"""Кодек NDTP: пример из спецификации, roundtrip ячеек, handshake, ответ сервера."""

import struct
from dataclasses import replace

import pytest

from transit_core.ndtp import (
    Cell,
    NavCell,
    ParseError,
    crc16_modbus,
    decode_frame,
    encode_handshake,
    encode_realtime,
    encode_result,
    frame_fields,
    make_cell,
)
from transit_core.ndtp.cells import LAYOUTS
from transit_core.ndtp.codec import NPL_LEN

NAV = NavCell(
    timestamp=1_767_690_000,
    lon=37.617321,
    lat=55.7551234,
    valid=True,
    speed_kmh=42,
    speed_max_kmh=47,
    course=181,
    track_m=1234,
    altitude_m=150,
    nsat=12,
    pdop=9,
    bat_voltage=200,
)
NAV_DECODED = replace(NAV, flags=0b1110_0000)  # после разбора flags = сырой байт extraDop


def test_crc16_modbus_reference_vector():
    assert crc16_modbus(b"123456789") == 0x4B37


def test_spec_example_coordinates_decode_to_moscow_north_east():
    # Пример из спецификации §6.1: 376173210 / 557551234, биты N/E.
    frame = decode_frame(encode_realtime(1166336, 1, NAV))
    nav_payload = frame.raw[NPL_LEN + 10 + 2 :]
    _, lon, lat, dop = struct.unpack_from("<IIIB", nav_payload)
    assert (lon, lat) == (376173210, 557551234)
    assert dop == 0b1110_0000
    assert frame.nav == NAV_DECODED
    assert frame.crc_ok


def test_crc_is_stored_byte_swapped_over_nph_and_body():
    raw = encode_handshake(7, 1)
    crc = crc16_modbus(raw[NPL_LEN:])
    assert raw[6:8] == struct.pack(">H", crc)


def test_southern_western_and_invalid_points_roundtrip():
    nav = NavCell(timestamp=1, lon=-70.5, lat=-33.25, valid=False, flags=0b0000_0011)
    got = decode_frame(encode_realtime(5, 2, nav)).nav
    assert got is not None
    assert (got.lon, got.lat, got.valid) == (-70.5, -33.25, False)
    assert got.flags == 0b0000_0011


def test_zero_coordinates_invalid_point_like_terminal():
    nav = NavCell(timestamp=10, lon=0.0, lat=0.0, valid=False)
    got = decode_frame(encode_realtime(5, 2, nav)).nav
    assert got is not None and got.lon == 0.0 and got.lat == 0.0 and not got.valid


@pytest.mark.parametrize("type_", sorted(LAYOUTS))
def test_every_known_cell_roundtrips(type_):
    lay = LAYOUTS[type_]
    fields = {name: i + 1 for i, name in enumerate(lay.names)}
    cell = Cell(type_, 1, lay.name, fields)
    raw = encode_realtime(42, 9, NAV, [cell])
    frame = decode_frame(raw)
    assert frame.cells == (cell,)
    assert encode_realtime(42, 9, frame.nav, frame.cells) == raw


def test_cell_sizes_match_spec():
    sizes = {lay.name: lay.fmt.size for lay in LAYOUTS.values()}
    assert sizes == {
        "G6CellIntSensor02": 26,
        "G6CellUsi08": 6,
        "G6CellCan10": 37,
        "G6CellLls15": 50,
        "G6CellTermo16": 8,
    }


def test_signed_fields_keep_sign():
    cell = make_cell("G6CellTermo16", temp=-25)
    frame = decode_frame(encode_realtime(1, 1, NAV, [cell]))
    assert frame.cells[0].fields["temp"] == -25


def test_unknown_cell_stops_parsing_without_error_keeping_previous():
    usi = make_cell("G6CellUsi08", level_l=77)
    raw = encode_realtime(1, 1, NAV, [usi])
    unknown = bytes([3, 0]) + b"\x00" * 40  # G6CellCrown03 — не разбираем
    body = raw[NPL_LEN:] + unknown + bytes([8, 1]) + b"\x00" * 6
    patched = _reframe(raw, body)
    frame = decode_frame(patched)
    assert frame.nav == NAV_DECODED
    assert frame.cells == (usi,)
    assert frame.crc_ok


def test_truncated_cell_is_parse_error():
    raw = encode_realtime(1, 1, NAV, [make_cell("G6CellCan10")])
    with pytest.raises(ParseError):
        decode_frame(_reframe(raw, raw[NPL_LEN:-5]))


def test_handshake_layout():
    frame = decode_frame(encode_handshake(1166336, 1))
    assert (frame.service_id, frame.nph_type, frame.request_id) == (0, 100, 1)
    assert frame.handshake == {
        "proto_version_high": 6,
        "proto_version_low": 2,
        "flags": 0,
        "peer_address": 1166336,
        "max_packet_size": 65535,
    }
    assert len(frame.raw) == 15 + 10 + 18


def test_result_echoes_request_id_and_code():
    request = decode_frame(encode_realtime(77, 123, NAV))
    reply = decode_frame(encode_result(request, code=5))
    assert (reply.service_id, reply.nph_type, reply.request_id) == (0, 0, 123)
    assert reply.unit_id == 77 and reply.crc_ok
    assert reply.raw[-4:] == struct.pack("<I", 5)


def test_frame_fields_for_nav_and_extra_cells():
    usi = make_cell("G6CellUsi08", number=1, level_l=160, temperature=20)
    fields = frame_fields(decode_frame(encode_realtime(99, 1, NAV, [usi])))
    assert fields["cell"] == "G6CellNav00"
    assert fields["unit_id"] == 99
    assert (fields["lon"], fields["lat"], fields["valid"]) == (37.617321, 55.7551234, True)
    assert (fields["speed_kmh"], fields["course"], fields["nsat"]) == (42, 181, 12)
    assert fields["usi08_1.level_l"] == 160


def test_frame_fields_for_handshake():
    fields = frame_fields(decode_frame(encode_handshake(3, 1)))
    assert fields["cell"] == "handshake" and fields["proto"] == "6.2"


def _reframe(raw: bytes, nph_and_body: bytes) -> bytes:
    """Пересобирает NPL под новое NPH+тело с корректным CRC."""
    crc = struct.pack(">H", crc16_modbus(nph_and_body))
    size = struct.pack("<H", len(nph_and_body))
    return raw[:2] + size + raw[4:6] + crc + raw[8:NPL_LEN] + nph_and_body
