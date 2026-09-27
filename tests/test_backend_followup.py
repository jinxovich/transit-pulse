"""Follow-up цели инцидента: карточка обновляется и после выхода цели из окна (T+10, T+15].

Инцидент открывается на визите окна, но через пару сим-минут упреждение цели < 10 мин и
она выпадает из окна. Пока цель не пройдена, проход отдельно прогнозирует её (follow-up):
в том же ML-батче, но не в ``VehicleState.prediction``, ``window_preds`` и ``can_open``.
"""

from __future__ import annotations

import asyncio
from collections import namedtuple
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from backend_kit import (
    FakeWall,
    feed_rows,
    has_raw,
    make_runtime,
    ml_transport,
    real_static,
    real_traffic,
    session,
)

from services.backend.app.ml_client import MlClient
from services.backend.app.pipeline.assemble import assemble, assemble_follow, ml_items
from services.backend.app.pipeline.mlpass import run_ml
from services.backend.app.pipeline.prepare import (
    VehicleInput,
    VehicleTask,
    VisitTask,
    prepare_vehicle,
)
from services.backend.app.pipeline.scheduler import PipelineRunner
from services.backend.app.state.store import TRACK_COLUMNS
from services.backend.app.state.timefmt import parse

T = datetime(2026, 1, 6, 8, 0, 0)
START = datetime(2026, 1, 6, 7, 0, 0)
Row = namedtuple("Row", "visit_id stop_key name lon lat tb")
needs_data = pytest.mark.skipif(not has_raw(), reason="нет data/raw")


def _visit(visit_id: int, lead: float, cur_dev: float) -> VisitTask:
    row = Row(visit_id, f"s{visit_id}", f"Остановка {visit_id}", 37.6, 55.7,
              T + timedelta(minutes=lead))  # fmt: skip
    return VisitTask(f"7:{visit_id}", str(visit_id), 1, row, lead,
                     {"cur_dev": cur_dev, "lead": lead})  # fmt: skip


def _task_with_follow() -> VehicleTask:
    """ТС 7: в окне зелёный визит 72, цель инцидента 70 уже в 6 мин (вне окна)."""
    task = VehicleTask("7", 7, False, True, cur_dev=0.0, horizon_ok=True)
    task.visits = [_visit(72, 12.0, 0.0)]
    task.follow = _visit(70, 6.0, 300.0)
    return task


def test_follow_visit_goes_to_same_ml_batch_but_not_to_window_predictions():
    bodies: list[dict] = []
    client = MlClient("http://ml", now=FakeWall(), transport=ml_transport(bodies=bodies))
    task = _task_with_follow()

    res = asyncio.run(run_ml(client, [task], {"7": "70"}))

    assert {i for i, _ in ml_items([task])} == {"7:72", "7:70"}
    assert {i["id"] for i in bodies[0]["items"]} == {"7:72", "7:70"}
    assert {i["id"] for i in bodies[1]["items"]} == {"7:70"}  # SHAP — как цели инцидента
    window = assemble(task, res.items, T, "test-ml")
    assert [vp.visit.visit_id for vp in window] == ["72"]
    follow = assemble_follow(task, res.items, T, "test-ml")
    assert follow is not None and follow.visit.visit_id == "70"
    p = follow.prediction
    assert p.model_mode == "ml" and p.risk_level == "red" and not p.horizon_ok
    assert p.lead_min == 6.0 and follow.cause.code


def test_follow_prediction_never_opens_incident():
    from services.backend.app.alerts.engine import IncidentBook

    task = _task_with_follow()
    client = MlClient("http://ml", now=FakeWall(), transport=ml_transport())
    res = asyncio.run(run_ml(client, [task], {}))
    follow = assemble_follow(task, res.items, T, "test-ml")

    assert follow.prediction.risk_level == "red"
    assert not IncidentBook().can_open("7", follow, T)


def test_no_follow_without_active_incident():
    task = VehicleTask("7", 7, False, True, cur_dev=0.0, horizon_ok=True)
    task.visits = [_visit(72, 12.0, 0.0)]

    assert assemble_follow(task, None, T, "test-ml") is None
    assert [i for i, _ in ml_items([task])] == ["7:72"]


def _has_stops_around(plan, t: datetime) -> bool:
    """В плане есть визиты до ``t``, в ``(t, t+10]`` и в окне ``(t+10, t+15]``."""
    tb = plan["tb"]
    m10, m15 = t + timedelta(minutes=10), t + timedelta(minutes=15)
    return (tb <= t).any() and ((tb > t) & (tb <= m10)).any() and ((tb > m10) & (tb <= m15)).any()


def _vehicle_input(t: datetime) -> tuple[VehicleInput, object]:
    """Реальное ТС validate с треком за 20 мин до ``t`` и визитами вокруг ``t``."""
    static, tr = real_static(), real_traffic()
    rows = tr[(tr["et"] > t - timedelta(minutes=20)) & (tr["et"] <= t)]
    ids = [int(x) for x in rows["tr_id"].unique() if int(x) in static.plans]
    tr_id = next(x for x in ids if _has_stops_around(static.plans[x], t))
    own = rows[rows["tr_id"] == tr_id].sort_values("et")
    points = [(r.et.to_pydatetime(), r.lon, r.lat, r.speed, r.heading, bool(r.valid))
              for r in own[TRACK_COLUMNS].itertuples()]  # fmt: skip
    return VehicleInput(str(tr_id), tr_id, points, False, True), static.plans[tr_id]


@needs_data
def test_prepare_follows_incident_target_only_between_now_and_window():
    vi, plan = _vehicle_input(START)
    soon = plan[(plan["tb"] > START) & (plan["tb"] <= START + timedelta(minutes=10))]
    window = plan[(plan["tb"] > START + timedelta(minutes=10))
                  & (plan["tb"] <= START + timedelta(minutes=15))]  # fmt: skip
    past = plan[plan["tb"] <= START]

    def follow_of(visit_id) -> VisitTask | None:
        return prepare_vehicle(real_static(), replace(vi, follow=str(int(visit_id))), START).follow

    f = follow_of(soon["visit_id"].iloc[-1])
    assert f is not None and 0 < f.lead_min <= 10
    assert f.features["lead"] == pytest.approx(f.lead_min, abs=0.01)
    assert follow_of(window["visit_id"].iloc[0]) is None  # и так в окне
    assert follow_of(past["visit_id"].iloc[-1]) is None  # цель уже пройдена по плану
    assert prepare_vehicle(real_static(), vi, START).follow is None


@pytest.fixture(scope="module")
def follow_run():
    """40 сим-минут с красным ML, прогноз которого растёт по мере приближения цели."""
    if not has_raw():
        pytest.skip("нет data/raw")
    wall = FakeWall()
    rt = make_runtime(wall, transport=ml_transport(delay_add=400.0, lead_slope=40.0))
    t_from = START - timedelta(minutes=20)
    session(rt, "s1", t_from)
    runner, events, snaps = PipelineRunner(rt), [], []
    rt.listeners.append(lambda kind, payload: events.append((kind, payload)))
    tr, t = real_traffic(), t_from
    for _ in range(40):
        rows = tr[(tr["et"] >= t) & (tr["et"] < t + timedelta(minutes=1))]
        feed_rows(rt, rows.sort_values("et", kind="stable"), wall, t_from)
        t += timedelta(minutes=1)
        wall.t = 1000.0 + (t - t_from).total_seconds() / rt.clock.speed
        if (due := runner.due()) is not None:
            asyncio.run(runner.run_pass(due))
            snaps.append((due, dict(rt.book.active_targets()), {
                vid: (rec.prediction, dict(rec.window_preds), rec.tr_id)
                for vid, rec in rt.store.vehicles.items() if rec.kind == "scheduled"
            }))  # fmt: skip
    return rt, events, snaps


@needs_data
def test_incident_keeps_updating_after_target_leaves_window(follow_run):
    _, events, _ = follow_run
    opened = {inc.id: inc for kind, inc in events if kind == "incident.opened"}
    late = [
        inc for kind, inc in events
        if kind == "incident.updated" and inc.ack is None
        and parse(inc.target_stop.planned_at) - parse(inc.updated_at) < timedelta(minutes=10)
    ]  # fmt: skip
    assert opened and late, "цель вне окна (lead < 10) — карточка всё равно обновляется"
    for inc in late:
        assert inc.lead_min == opened[inc.id].lead_min  # упреждение на момент создания
        assert inc.predicted_delay_s != opened[inc.id].predicted_delay_s


@needs_data
def test_follow_up_never_leaks_into_vehicle_prediction_or_window(follow_run):
    rt, events, snaps = follow_run
    assert any(targets for _, targets, _ in snaps)
    for t, _, states in snaps:
        for pred, window, tr_id in states.values():
            if pred is not None and pred.horizon_ok:
                assert 10 < pred.lead_min <= 15
            elif pred is not None:
                assert pred.lead_min > 15
            plan = rt.static.plans[tr_id].set_index("visit_id")["tb"]
            for visit_id in window:  # только визиты окна, без follow-up (lead < 10)
                assert plan[int(visit_id)] > t + timedelta(minutes=10)
    for kind, inc in events:
        if kind == "incident.opened":
            assert 10 < inc.lead_min <= 15
