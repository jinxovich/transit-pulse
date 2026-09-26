"""Map matching в бэкенде: привязка на ingest, отклонение и следующая остановка в VehicleState."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from backend_kit import FakeWall, feed_rows, has_raw, make_runtime, real_traffic, session

from services.backend.app.pipeline.scheduler import PipelineRunner
from services.backend.app.state.matched import matched_now
from transit_core.matching import RouteMatcher

T_FROM = datetime(2026, 1, 6, 6, 40, 0)
T_TO = T_FROM + timedelta(minutes=20)


@pytest.fixture(scope="module")
def played():
    """20 сим-минут validate, проигранных в state, и один проход прогнозов в конце."""
    if not has_raw():
        pytest.skip("нет data/raw")
    wall = FakeWall()
    rt = make_runtime(wall)
    session(rt, "m1", T_FROM)
    tr = real_traffic()
    rows = tr[(tr["et"] >= T_FROM) & (tr["et"] < T_TO)].sort_values("et", kind="stable")
    feed_rows(rt, rows, wall, T_FROM)
    runner = PipelineRunner(rt)
    t = runner.due()
    assert t is not None
    asyncio.run(runner.run_pass(t))
    return rt, rows, t


def _scheduled(rt):
    return [r for r in rt.store.vehicles.values() if r.kind == "scheduled"]


def test_every_point_goes_through_matcher(played):
    rt, rows, _ = played
    recs = _scheduled(rt)
    assert recs and all(isinstance(r.matcher, RouteMatcher) for r in recs)
    for rec in recs:
        fed = int((rows["unit_id"] == rec.unit_id).sum())
        assert rec.matcher.stats["points"] == fed
    assert sum(r.match is not None and r.match.on_route for r in recs) >= len(recs) // 2


def test_vehicle_state_uses_confident_match(played):
    rt, _, t = played
    used = 0
    for rec in _scheduled(rt):
        mn = matched_now(rt.static, rec, t)
        if mn is None:
            continue
        used += 1
        assert rec.current_dev_s == pytest.approx(round(mn.dev_s, 1))
        if mn.next_row is not None:
            assert rec.next_stop.visit_id == str(int(mn.next_row.visit_id))
            assert rec.match.dist_to_next_m >= 0
    assert used > 0


def test_stale_match_falls_back(played):
    rt, _, t = played
    rec = next(r for r in _scheduled(rt) if r.match is not None)
    assert matched_now(rt.static, rec, t + timedelta(minutes=10)) is None
