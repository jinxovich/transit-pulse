"""Снимок дверей из NDTP (IRMA / «Корона») в state и в деталях ТС."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from backend_kit import FakeWall, has_raw, make_runtime
from fastapi.testclient import TestClient

from services.backend.app.ingest.codec import NavCell, encode_realtime
from services.backend.app.ingest.doors import door_snapshot
from services.backend.app.ingest.worker import apply_frame
from services.backend.app.main import create_app
from services.backend.app.state.timefmt import to_epoch
from transit_core import schemas as S
from transit_core.ndtp import decode_frame, make_cell

T0 = datetime(2026, 1, 6, 7, 0, 0)
UNIT, TR = 663271, 116445
needs_data = pytest.mark.skipif(not has_raw(), reason="нет data/raw")

IRMA = make_cell(
    "G6CellIrma04", irma_door_in1=3, irma_door_in2=2, irma_door_out1=1, irma_door_out4=4,
    irma_present_door1=True, irma_present_door2=True, irma_present_door3=True,
    irma_closed_door2=True, irma_closed_door4=True,  # у 4-й датчика нет — не «открыта»
)  # fmt: skip
CROWN = make_cell("G6CellCrown03", corona_door_in1=5, corona_door_out2=6)


def _frame(et: datetime, cells=(), lon=37.6):
    nav = NavCell(to_epoch(et), lon, 55.75, True, 20, 20, 90)
    return decode_frame(encode_realtime(UNIT, 1, nav, cells))


def test_irma_snapshot_counts_and_open_doors():
    snap = door_snapshot([IRMA], T0)
    assert snap == S.DoorSnapshot(
        at="2026-01-06T07:00:00", source="irma", any_open=True, open_doors=[1, 3],
        entered=5, exited=5,
    )  # fmt: skip


def test_irma_all_closed_is_not_open():
    closed = make_cell("G6CellIrma04", irma_present_door1=True, irma_closed_door1=True)
    snap = door_snapshot([closed], T0)
    assert snap is not None and snap.any_open is False and snap.open_doors == []


def test_crown_has_counters_but_no_door_status():
    snap = door_snapshot([CROWN], T0)
    assert snap is not None and snap.source == "corona"
    assert (snap.any_open, snap.open_doors, snap.entered, snap.exited) == (None, [], 5, 6)


def test_irma_preferred_over_crown_and_no_cells_gives_none():
    assert door_snapshot([CROWN, IRMA], T0).source == "irma"
    assert door_snapshot([make_cell("G6CellUsi08")], T0) is None


@needs_data
def test_backend_keeps_last_door_snapshot_and_shows_it_in_detail():
    rt = make_runtime(FakeWall())
    with TestClient(create_app(rt, background=False)) as client:
        assert apply_frame(rt, 0.0, _frame(T0))
        rec = rt.store.vehicles[str(TR)]
        assert rec.doors is None  # ячеек дверей не было — поля нет

        door_frame = _frame(T0 + timedelta(seconds=10), [IRMA], lon=37.6001)
        rt.stats.on_frame(door_frame, 0.0)
        assert apply_frame(rt, 0.0, door_frame)
        # Следующий пакет без дверей не стирает последний снимок.
        assert apply_frame(rt, 0.0, _frame(T0 + timedelta(seconds=20), lon=37.6002))
        assert rec.doors is not None and rec.doors.at == "2026-01-06T07:00:10"

        resp = client.get(f"/api/v1/vehicles/{TR}")
        assert resp.status_code == 200, resp.text
        doors = S.VehicleDetail.model_validate(resp.json()).doors
        assert doors is not None and doors.open_doors == [1, 3] and doors.entered == 5

        stats = S.IngestStats.model_validate(client.get("/api/v1/ingest/stats").json())
        assert stats.last_packet is not None
        assert stats.last_packet.fields["irma04_0.irma_door_in1"] == 3
