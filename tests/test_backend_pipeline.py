"""Проход прогнозов: инвариант горизонта, инциденты без «задним числом», fallback ML."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from backend_kit import (
    FakeWall,
    feed_rows,
    has_raw,
    make_runtime,
    ml_transport,
    real_traffic,
    session,
)

from services.backend.app.ml_client import FALLBACK_VERSION, CircuitBreaker, MlClient
from services.backend.app.pipeline.scheduler import PipelineRunner
from services.backend.app.state.timefmt import parse

START = datetime(2026, 1, 6, 7, 0, 0)
needs_data = pytest.mark.skipif(not has_raw(), reason="нет data/raw")


def _play(rt, wall, t_from: datetime, t_to: datetime, origin: datetime) -> None:
    tr = real_traffic()
    rows = tr[(tr["et"] >= t_from) & (tr["et"] < t_to)].sort_values("et", kind="stable")
    feed_rows(rt, rows, wall, origin)


def _run_passes(rt, wall, t_from: datetime, minutes: int, step_min: int = 1) -> list:
    """Проигрывает поток поминутно и делает проход прогнозов на каждой минуте."""
    runner, events = PipelineRunner(rt), []
    rt.listeners.append(lambda kind, payload: events.append((kind, payload)))
    t = t_from
    for _ in range(0, minutes, step_min):
        _play(rt, wall, t, t + timedelta(minutes=step_min), t_from)
        t += timedelta(minutes=step_min)
        wall.t = 1000.0 + (t - t_from).total_seconds() / rt.clock.speed
        due = runner.due()
        if due is not None:
            asyncio.run(runner.run_pass(due))
    return events


@pytest.fixture(scope="module")
def red_run():
    """40 сим-минут validate с «пессимистичным» ML: много красных прогнозов."""
    if not has_raw():
        pytest.skip("нет data/raw")
    wall = FakeWall()
    rt = make_runtime(wall, transport=ml_transport(delay_add=400.0))
    t_from = START - timedelta(minutes=20)
    session(rt, "s1", t_from)
    events = _run_passes(rt, wall, t_from, 40)
    return rt, events


@needs_data
def test_predictions_respect_horizon_invariant(red_run):
    rt, _ = red_run
    now = rt.sim_now()
    preds = [s.prediction for s in rt.vehicle_states().values() if s.prediction]
    assert preds, "у ТС с расписанием должны появиться прогнозы"
    for p in preds:
        assert parse(p.generated_at) <= now
        if p.horizon_ok:
            assert 10 < p.lead_min <= 15
            planned = parse(p.target_stop.planned_at)
            assert timedelta(minutes=10) < planned - parse(p.generated_at) <= timedelta(minutes=15)
        else:
            assert p.lead_min > 15


@needs_data
def test_incidents_open_on_red_and_never_retroactively(red_run):
    rt, events = red_run
    opened = [inc for kind, inc in events if kind == "incident.opened"]
    assert opened, "красный прогноз в горизонте должен открыть инцидент"
    for inc in opened:
        assert parse(inc.target_stop.planned_at) > parse(inc.created_at)
        assert 10 < inc.lead_min <= 15
        assert inc.recommendations and inc.cause.title
    active = {}
    for kind, inc in events:
        if kind == "incident.opened":
            assert inc.vehicle_id not in active, "не больше одного активного инцидента на ТС"
            active[inc.vehicle_id] = inc.id
        elif kind == "incident.resolved":
            active.pop(inc.vehicle_id, None)
            assert inc.outcome in ("hit", "false_alarm")


@needs_data
def test_model_mode_ml_when_service_ok(red_run):
    rt, _ = red_run
    fresh = [s.prediction for s in rt.vehicle_states().values() if s.prediction and not s.stale]
    assert fresh and all(p.model_mode == "ml" and p.model_version == "test-ml" for p in fresh)


@needs_data
def test_warming_up_vehicles_have_no_predictions():
    wall = FakeWall()
    rt = make_runtime(wall)
    session(rt, "s1", START)
    _run_passes(rt, wall, START, 5)
    states = [s for s in rt.vehicle_states().values() if s.kind == "scheduled"]
    assert states and all(s.warming_up and s.prediction is None for s in states)
    assert rt.book.incidents == {}


@needs_data
def test_ml_down_falls_back_to_heuristic():
    wall = FakeWall()
    rt = make_runtime(wall, transport=ml_transport(fail=True))
    t_from = START - timedelta(minutes=20)
    session(rt, "s1", t_from)
    _run_passes(rt, wall, t_from, 24)
    preds = [s.prediction for s in rt.vehicle_states().values() if s.prediction]
    assert preds and all(p.model_mode == "fallback" for p in preds)
    assert all(p.model_version == FALLBACK_VERSION for p in preds)
    assert rt.ml.status in ("down", "degraded") and rt.ml.breaker.failures >= 3
    assert rt.system_status().model_mode == "fallback"


def test_circuit_breaker_opens_after_three_failures_and_recovers():
    wall = FakeWall()
    calls: list[str] = []
    client = MlClient("http://ml", now=wall, transport=ml_transport(fail=True, calls=calls))
    items = [("1:1", {"cur_dev": 10.0})]

    async def go():
        for _ in range(5):
            assert await client.predict(items) is None
        return len(calls)

    assert asyncio.run(go()) == 3  # после 3 ошибок запросы в ml не идут
    assert client.status == "down"
    wall.advance(16.0)
    client.http._transport = ml_transport(calls=calls)  # ml поднялся
    out = asyncio.run(client.predict(items))
    assert out is not None and out["1:1"].delay_s == 10.0
    assert client.status == "ok"


def test_breaker_half_open_failure_reopens():
    wall = FakeWall()
    br = CircuitBreaker(3, 15.0, wall)
    for _ in range(3):
        br.failure()
    assert not br.allow()
    wall.advance(15.1)
    assert br.allow()
    br.failure()
    assert not br.allow()


@needs_data
def test_sqlite_journal_records_predictions_and_incidents(red_run):
    rt, _ = red_run
    db = rt.db.conn
    assert db.execute("SELECT count(*) FROM predictions").fetchone()[0] > 0
    kinds = {k for (k,) in db.execute("SELECT DISTINCT kind FROM incident_events")}
    assert "incident.opened" in kinds


@needs_data
def test_quality_on_stream_counts_vehicle_hours_and_policy(red_run):
    rt, _ = red_run
    q = rt.journal.contract(rt.book)

    assert rt.journal.vehicle_hours() > 0
    assert q.n_incidents == len(rt.book.incidents) > 0
    assert q.alerts_per_vehicle_hour == round(q.n_incidents / rt.journal.vehicle_hours(), 2)
    assert q.lead_median_min is not None and 10 < q.lead_median_min <= 15
    assert q.alert_policy is not None and q.alert_policy.min_streak == rt.book.policy.min_streak
    assert sum(b.n for b in q.mae_by_lead) == len(rt.journal.errors)


def test_load_offline_reads_stream_model_cv_from_ml_metrics(tmp_path):
    import json

    from services.backend.app.alerts.quality import load_offline

    (tmp_path / "metrics.json").write_text(json.dumps({
        "submission": {"baseline_cur_dev": 88.4, "catboost_synthetic_mae": 67.1},
        "stream": {"baseline_cur_dev": 114.1, "catboost_synthetic_mae": 77.8},
    }))

    offline = load_offline(tmp_path)

    assert offline.cv_mae_baseline_s == 114.1
    assert offline.cv_mae_model_s == 77.8
    assert offline.improvement == round(1 - 77.8 / 114.1, 4)


def test_standing_before_next_trip_start_is_planned_layover():
    from collections import namedtuple

    from services.backend.app.pipeline.prepare import VehicleTask

    row = namedtuple("Row", "new_trip")
    at_terminal = VehicleTask("1", 1, stale=False, ready=True, dwell_s=900.0, next_row=row(1))
    mid_trip = VehicleTask("1", 1, stale=False, ready=True, dwell_s=900.0, next_row=row(0))

    assert at_terminal.extras()["in_layover"] == 1.0
    assert "in_layover" not in mid_trip.extras()
