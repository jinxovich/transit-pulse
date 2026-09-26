"""Эталон: реальные байты официального эмулятора (``tests/fixtures/emu.bin``).

Снято ``scripts/ndtp_capture.py`` с образа ``ndtp-telemetry-emulator:1.0`` по конфигу
``tests/fixtures/emu_config.json`` (юнит 990001 — autoGenerate с набором по умолчанию,
990002 — Nav00 + Lls15 + два Usi08), ~13 с по 1 пакету в секунду. Файл —
конкатенация двух TCP-потоков целиком.

Подтверждено на этих байтах: CRC-16/Modbus по NPH + телу, в NPL лежит со
свапнутыми байтами (big-endian) — ровно как в спецификации, все кадры ``crc_ok``.
"""

from collections import Counter, defaultdict
from pathlib import Path

import pytest

from transit_core.ndtp import FrameDecoder, encode_handshake, encode_realtime

EMU = Path(__file__).parent / "fixtures" / "emu.bin"
DEFAULT_SET = ["G6CellUsi08", "G6CellTermo16", "G6CellIntSensor02", "G6CellCan10"]
EXPLICIT_SET = ["G6CellLls15", "G6CellUsi08", "G6CellUsi08"]


@pytest.fixture(scope="module")
def decoded():
    dec = FrameDecoder()
    frames = dec.feed(EMU.read_bytes())
    return dec, frames


def test_whole_capture_parses_without_errors(decoded):
    dec, frames = decoded
    assert len(frames) >= 20
    assert (dec.parse_errors, dec.resyncs, dec.pending) == (0, 0, 0)


def test_crc_matches_spec_on_real_bytes(decoded):
    dec, frames = decoded
    assert dec.crc_errors == 0
    assert all(f.crc_ok for f in frames)


def test_each_unit_starts_with_handshake_then_realtime(decoded):
    _, frames = decoded
    by_unit = defaultdict(list)
    for f in frames:
        by_unit[f.unit_id].append(f)
    assert set(by_unit) == {990001, 990002}
    for unit_frames in by_unit.values():
        first, *rest = unit_frames
        assert first.handshake is not None and first.handshake["peer_address"] == first.unit_id
        assert rest and all((f.service_id, f.nph_type) == (1, 101) for f in rest)
        ids = [f.request_id for f in unit_frames]
        assert ids == list(range(1, len(ids) + 1))


def test_cell_sets_match_config(decoded):
    _, frames = decoded
    for f in frames:
        if f.nav is None:
            continue
        names = [c.name for c in f.cells]
        assert names == (DEFAULT_SET if f.unit_id == 990001 else EXPLICIT_SET)
        assert Counter(c.number for c in f.cells if c.name == "G6CellUsi08") == Counter(
            range(names.count("G6CellUsi08"))
        )


def test_nav_is_valid_and_near_moscow(decoded):
    _, frames = decoded
    navs = [f.nav for f in frames if f.nav]
    assert all(n.valid for n in navs)
    assert all(55.5 < n.lat < 56.0 and 37.3 < n.lon < 37.9 for n in navs)
    assert all(0 <= n.course <= 360 and n.nsat > 0 for n in navs)


def test_can_speed_matches_nav_speed(decoded):
    # Косвенная проверка раскладки Can10: эмулятор копирует скорость навигации.
    _, frames = decoded
    for f in frames:
        for cell in f.cells:
            if cell.name == "G6CellCan10":
                assert cell.fields["speed"] == f.nav.speed_kmh


def test_encoder_reproduces_emulator_bytes_exactly(decoded):
    _, frames = decoded
    for f in frames:
        if f.handshake is not None:
            assert encode_handshake(f.unit_id, f.request_id) == f.raw
        else:
            assert encode_realtime(f.unit_id, f.request_id, f.nav, f.cells) == f.raw
