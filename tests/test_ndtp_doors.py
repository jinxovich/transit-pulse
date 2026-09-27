"""Ячейки дверей и пассажиропотока: ``G6CellCrown03`` (type 3) и ``G6CellIrma04`` (type 4).

Эталон ``tests/fixtures/emu_doors.bin`` собран классами самого эмулятора из образа
``ndtp-telemetry-emulator:1.0`` (``CellFactory.createPrototype`` + ``CellReflectionMapper``
+ ``CellFactory.wrapNphPacket``): unit 990003, requestId 7, ячейки Nav00, Irma04 (номер 0),
Crown03 (номер 1). В спецификации у этих ячеек только список полей, раскладка —
из javolution-структур эмулятора: Crown03 = u32 odometer, u16 zone, 8×u8 счётчиков
(14 байт); Irma04 — то же плюс байт флагов: present_door1…4 в битах 0…3,
closed_door1…4 в битах 4…7 (15 байт).
"""

from pathlib import Path

import pytest

from transit_core.ndtp import (
    FrameDecoder,
    NavCell,
    decode_frame,
    encode_realtime,
    frame_fields,
    make_cell,
)
from transit_core.ndtp.cells import IRMA_FLAGS
from transit_core.ndtp.codec import NPL_LEN

DOORS_BIN = Path(__file__).parent / "fixtures" / "emu_doors.bin"
NAV = NavCell(timestamp=1_767_690_000, lon=37.6, lat=55.75, valid=True, speed_kmh=12, course=90)
IRMA_PAYLOAD_OFFSET = NPL_LEN + 10 + 2 + 26 + 2  # NPL, NPH, [0,0]+Nav00, [4,n]


def _irma(**fields):
    return make_cell("G6CellIrma04", **fields)


def test_emulator_reference_frame_decodes_completely():
    dec = FrameDecoder()
    frames = dec.feed(DOORS_BIN.read_bytes())
    assert (dec.parse_errors, dec.crc_errors, dec.pending) == (0, 0, 0)
    (frame,) = frames
    assert frame.crc_ok and (frame.unit_id, frame.request_id) == (990003, 7)
    assert frame.nav is not None and frame.nav.speed_kmh == 23
    irma, crown = frame.cells
    assert (irma.name, irma.number, crown.name, crown.number) == (
        "G6CellIrma04", 0, "G6CellCrown03", 1,
    )  # fmt: skip
    assert irma.fields["odometer"] == 123456 and irma.fields["zone"] == 42
    ins = [irma.fields[f"irma_door_in{d}"] for d in range(1, 5)]
    outs = [irma.fields[f"irma_door_out{d}"] for d in range(1, 5)]
    assert (ins, outs) == ([5, 3, 0, 250], [2, 7, 1, 0])
    present = [irma.fields[f"irma_present_door{d}"] for d in range(1, 5)]
    closed = [irma.fields[f"irma_closed_door{d}"] for d in range(1, 5)]
    assert (present, closed) == ([True, True, False, False], [False, True, True, True])
    assert crown.fields["odometer"] == 654321 and crown.fields["zone"] == 7
    assert [crown.fields[f"corona_door_in{d}"] for d in range(1, 5)] == [11, 12, 13, 14]
    assert [crown.fields[f"corona_door_out{d}"] for d in range(1, 5)] == [21, 22, 23, 24]


def test_encoder_reproduces_emulator_reference_bytes():
    raw = DOORS_BIN.read_bytes()
    frame = decode_frame(raw)
    assert encode_realtime(frame.unit_id, frame.request_id, frame.nav, frame.cells) == raw


@pytest.mark.parametrize(
    ("flag", "bit"), [(name, i) for i, name in enumerate(IRMA_FLAGS)]
)  # fmt: skip
def test_irma_flag_bits_match_javolution_order(flag, bit):
    # Порядок бит снят с G6CellIrma04 эмулятора: present1 → 0x01 … closed4 → 0x80.
    raw = encode_realtime(1, 1, NAV, [_irma(**{flag: True})])
    assert raw[IRMA_PAYLOAD_OFFSET + 14] == 1 << bit
    got = decode_frame(raw).cells[0].fields
    assert [n for n in IRMA_FLAGS if got[n]] == [flag]


def test_irma_roundtrip():
    cell = _irma(
        odometer=987_654, zone=3, irma_door_in1=4, irma_door_out4=9,
        irma_present_door1=True, irma_present_door3=True, irma_closed_door3=True,
    )  # fmt: skip
    frame = decode_frame(encode_realtime(7, 3, NAV, [cell]))
    assert frame.crc_ok and frame.cells == (cell,)


def test_crown_roundtrip():
    cell = make_cell("G6CellCrown03", 2, odometer=1, zone=65535, corona_door_in2=200)
    frame = decode_frame(encode_realtime(7, 3, NAV, [cell]))
    assert frame.crc_ok and frame.cells == (cell,)


def test_cells_after_door_cells_are_still_parsed():
    # Раньше type 3/4 были «неизвестными» и обрывали разбор всего, что шло за ними.
    usi = make_cell("G6CellUsi08", level_l=77)
    cells = [_irma(irma_present_door2=True), make_cell("G6CellCrown03"), usi]
    frame = decode_frame(encode_realtime(1, 1, NAV, cells))
    assert [c.name for c in frame.cells] == ["G6CellIrma04", "G6CellCrown03", "G6CellUsi08"]
    assert frame.cells[-1] == usi


def test_door_fields_visible_in_last_packet_view():
    fields = frame_fields(decode_frame(DOORS_BIN.read_bytes()))
    assert fields["irma04_0.irma_door_in4"] == 250
    assert fields["irma04_0.irma_closed_door2"] is True
    assert fields["crown03_1.corona_door_out1"] == 21
