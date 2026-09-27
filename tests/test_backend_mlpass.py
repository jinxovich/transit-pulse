"""Двухпроходный ML: SHAP-вклады запрашиваются только для рискованных прогнозов."""

from __future__ import annotations

import asyncio
from collections import namedtuple
from datetime import datetime

from backend_kit import FakeWall, ml_transport

from services.backend.app.alerts.policy import AlertPolicy
from services.backend.app.metrics import Metrics
from services.backend.app.ml_client import MlClient
from services.backend.app.pipeline.assemble import assemble
from services.backend.app.pipeline.mlpass import run_ml
from services.backend.app.pipeline.prepare import VehicleTask, VisitTask

T = datetime(2026, 1, 6, 8, 0, 0)
Row = namedtuple("Row", "visit_id stop_key name lon lat tb")


def _visit(vid: str, n: int, cur_dev: float) -> VisitTask:
    row = Row(int(vid) * 10 + n, f"s{vid}", f"Остановка {vid}", 37.6, 55.7,
              datetime(2026, 1, 6, 8, 12 + n))  # fmt: skip
    return VisitTask(f"{vid}:{row.visit_id}", str(row.visit_id), 1, row, 12.0 + n,
                     {"cur_dev": cur_dev, "lead": 12.0 + n})  # fmt: skip


def _task(vid: str, *devs: float, stale: bool = False) -> VehicleTask:
    task = VehicleTask(vid, int(vid), stale, True, cur_dev=devs[0])
    task.visits = [_visit(vid, n, d) for n, d in enumerate(devs)]
    return task


def _fleet() -> list[VehicleTask]:
    """1 — красный, 2 — зелёный, 3 — зелёный с активным инцидентом, 4 — красный, но stale."""
    return [_task("1", 300.0), _task("2", 0.0), _task("3", 10.0), _task("4", 300.0, stale=True)]


def _run(transport, targets=None):
    client = MlClient("http://ml", now=FakeWall(), transport=transport)
    return asyncio.run(run_ml(client, _fleet(), targets or {"3": "30"}))


def test_explain_requested_only_for_risky_and_active_incidents():
    bodies: list[dict] = []

    res = _run(ml_transport(bodies=bodies))

    assert [b["explain"] for b in bodies] == [False, True]
    assert {i["id"] for i in bodies[0]["items"]} == {"1:10", "2:20", "3:30"}
    assert {i["id"] for i in bodies[1]["items"]} == {"1:10", "3:30"}
    assert res.explained == 2 and res.predict_ms is not None and res.explain_ms is not None
    assert res.items["1:10"].contributions and res.items["3:30"].contributions
    assert res.items["2:20"].contributions == []


def test_only_visible_visits_are_explained_not_whole_window():
    bodies: list[dict] = []
    client = MlClient("http://ml", now=FakeWall(), transport=ml_transport(bodies=bodies))
    # первый визит жёлтый, дальше зелёный, красный, ещё красный
    task = _task("5", 70.0, 0.0, 300.0, 400.0)

    res = asyncio.run(run_ml(client, [task], {}))

    assert {i["id"] for i in bodies[1]["items"]} == {"5:50", "5:52"}
    assert res.explained == 2


def test_no_explain_call_when_everything_is_green():
    bodies: list[dict] = []
    client = MlClient("http://ml", now=FakeWall(), transport=ml_transport(bodies=bodies))

    res = asyncio.run(run_ml(client, [_task("2", 0.0)], {}))

    assert len(bodies) == 1 and bodies[0]["explain"] is False
    assert res.explain_ms is None and res.explained == 0


def test_predictions_survive_explain_failure_with_rule_based_cause():
    bodies: list[dict] = []

    res = _run(ml_transport(bodies=bodies, fail_explain=True))

    assert len(bodies) == 2 and res.items is not None
    assert set(res.items) == {"1:10", "2:20", "3:30"}
    assert all(it.contributions == [] for it in res.items.values())
    red = assemble(_fleet()[0], res.items, T, "test-ml")[0]
    assert red.prediction.model_mode == "ml" and red.prediction.risk_level == "red"
    assert red.cause.code == "ACCUMULATED_DELAY"  # правило по cur_dev, без SHAP


def test_ml_down_gives_no_items_and_no_latency():
    res = _run(ml_transport(fail=True))

    assert res.items is None and res.predict_ms is None


def test_metrics_split_predict_and_explain():
    m = Metrics()

    m.observe_ml(3.0, 20.0, 5)
    m.observe_ml(2.0, None, 0)

    assert m.stats("ml_predict").max_ms == 3.0
    assert m.stats("ml_explain").max_ms == 20.0
    assert m.stats("ml").max_ms == 23.0 and m.stats("ml").p50_ms == 12.5


def test_first_alert_policy_candidate_is_explained_even_if_not_first_red():
    """Инцидент откроется на визите, прошедшем политику алерта, а не на первом красном —
    причина в его карточке тоже должна быть по SHAP."""
    bodies: list[dict] = []
    client = MlClient("http://ml", now=FakeWall(), transport=ml_transport(bodies=bodies))
    task = _task("6", 130.0, 300.0)  # оба красные; политике (> 150 c) отвечает второй
    policy = AlertPolicy(delay_s=150.0, p_late=0.95, mode="or", min_streak=1)

    asyncio.run(run_ml(client, [task], {}, policy))

    assert {i["id"] for i in bodies[1]["items"]} == {"6:60", "6:61"}
