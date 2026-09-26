"""What-if: преобразование признаков мерой и ``POST /api/v1/whatif`` на данных validate."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta

import pytest
from backend_kit import DEAD_ML, FakeWall, feed_rows, has_raw, make_runtime, ml_transport
from backend_kit import real_traffic as traffic
from fastapi.testclient import TestClient

from services.backend.app.main import create_app
from services.backend.app.ml_client import FALLBACK_VERSION, MlClient
from services.backend.app.pipeline.scheduler import PipelineRunner
from services.backend.app.state.timefmt import parse
from services.backend.app.whatif.measures import Measure, apply_measure, guard, measure_of
from transit_core import schemas as S

START = datetime(2026, 1, 6, 7, 0, 0)
needs_data = pytest.mark.skipif(not has_raw(), reason="нет data/raw")
BEFORE = {"cur_dev": 300.0, "gps_dev": 240.0, "eta_dev": float("nan"), "spd5": 12.0,
          "trip_break_between": 1.0, "max_gap_between": 4.0}  # fmt: skip


def test_measure_defaults_and_explicit_value():
    assert measure_of(S.WhatIfRequest(vehicle_id="1", action="hold_at_stop")).minutes == 2.0
    assert measure_of(S.WhatIfRequest(vehicle_id="1", action="shorten_dwell")).minutes == 3.0
    req = S.WhatIfRequest(vehicle_id="1", action="shorten_dwell", value=5)
    assert measure_of(req).minutes == 5.0


def test_hold_shifts_deviation_and_keeps_input_intact():
    after = apply_measure(Measure("hold_at_stop", 2.0), BEFORE)

    assert (after["cur_dev"], after["gps_dev"]) == (420.0, 360.0)
    assert math.isnan(after["eta_dev"]) and after["spd5"] == 12.0
    assert BEFORE["cur_dev"] == 300.0


def test_shorten_dwell_is_capped_by_layover_and_schedule():
    cut3 = apply_measure(Measure("shorten_dwell", 3.0), BEFORE)
    cut10 = apply_measure(Measure("shorten_dwell", 10.0), BEFORE)
    small = apply_measure(Measure("shorten_dwell", 3.0), {**BEFORE, "cur_dev": 60.0})

    assert cut3["cur_dev"] == 120.0
    assert cut10["cur_dev"] == 60.0  # плановый отстой всего 4 мин
    assert small["cur_dev"] == 0.0  # не раньше графика


def test_skip_layover_and_reserve_act_only_after_trip_break():
    no_break = {**BEFORE, "trip_break_between": 0.0}

    assert apply_measure(Measure("skip_layover", 0.0), BEFORE)["cur_dev"] == 60.0
    assert apply_measure(Measure("add_reserve", 0.0), BEFORE)["cur_dev"] == 0.0
    for action in ("shorten_dwell", "skip_layover", "add_reserve"):
        assert apply_measure(Measure(action, 3.0), no_break)["cur_dev"] == 300.0


def test_guard_keeps_measures_monotonic():
    hold, cut = Measure("hold_at_stop", 2.0), Measure("shorten_dwell", 3.0)

    assert guard(hold, (100.0, 0.4), (90.0, 0.3)) == (100.0, 0.4)
    assert guard(cut, (60.0, 0.3), (-40.0, 0.1)) == (0.0, 0.1)  # не раньше графика
    assert guard(cut, (6.0, 0.3), (18.0, 0.4)) == (6.0, 0.3)  # не хуже, чем без меры
    assert guard(cut, (-30.0, 0.1), (-50.0, 0.1)) == (-30.0, 0.1)


@pytest.fixture(scope="module")
def live():
    """Бэкенд после 30 сим-минут потока; поддельный ML: ``delay = cur_dev``."""
    if not has_raw():
        pytest.skip("нет data/raw")
    wall = FakeWall()
    rt = make_runtime(wall, transport=ml_transport())
    t_from = START - timedelta(minutes=20)
    with TestClient(create_app(rt, background=False)) as client:
        body = {"session_id": "s-wi", "sim_time": t_from.isoformat(), "speed": 1.0,
                "state": "running", "warmup_until": None}  # fmt: skip
        client.post("/internal/sim/session", json=body)
        runner, tr = PipelineRunner(rt), traffic()
        for m in range(30):
            a, b = t_from + timedelta(minutes=m), t_from + timedelta(minutes=m + 1)
            feed_rows(rt, tr[(tr["et"] >= a) & (tr["et"] < b)].sort_values("et"), wall, t_from)
            wall.t = 1000.0 + (b - t_from).total_seconds()
            if (due := runner.due()) is not None:
                asyncio.run(runner.run_pass(due))
        yield client, rt


def _predicted(rt, in_window: bool = False) -> list[str]:
    return [vid for vid, r in rt.store.vehicles.items()
            if r.prediction is not None and (r.prediction.horizon_ok or not in_window)]  # fmt: skip


def _post(client, vid: str, action: str, value: float | None = None) -> S.WhatIfResult:
    resp = client.post("/api/v1/whatif", json={"vehicle_id": vid, "action": action, "value": value})
    assert resp.status_code == 200, resp.text
    return S.WhatIfResult.model_validate(resp.json())


@needs_data
def test_hold_at_stop_adds_minutes_to_every_stop(live):
    client, rt = live
    vid = _predicted(rt, in_window=True)[0]

    res = _post(client, vid, "hold_at_stop", 2)

    now = rt.sim_now()
    assert res.applied and res.model_mode == "ml" and res.value_min == 2.0
    assert res.current == rt.store.vehicles[vid].prediction
    assert res.target.visit_id == res.current.target_stop.visit_id
    assert res.delta_delay_s == 120.0
    for s in res.stops:
        assert now < parse(s.planned_at) <= now + timedelta(hours=1)
        assert s.predicted_after_s - s.predicted_before_s == pytest.approx(120.0, abs=0.2)
    assert any(s.in_window for s in res.stops)


@needs_data
def test_terminal_measures_change_only_stops_after_trip_break(live):
    client, rt = live
    with_break = 0
    for vid in _predicted(rt):
        res = _post(client, vid, "add_reserve")
        before_break = [s for s in res.stops if not s.after_break]
        assert all(s.predicted_after_s == s.predicted_before_s for s in before_break)
        after_break = [s for s in res.stops if s.after_break]
        assert all(s.predicted_after_s == min(s.predicted_before_s, 0.0) for s in after_break)
        cut = _post(client, vid, "shorten_dwell", 3)
        assert all(s.predicted_after_s <= s.predicted_before_s for s in cut.stops)
        with_break += bool(after_break)
        if not after_break:
            assert not res.applied and "нет конечной" in res.note
    assert with_break, "хотя бы у одного ТС в ближайший час есть конечная"


@needs_data
def test_whatif_errors(live, tmp_path):
    client, rt = live
    unknown = client.post("/api/v1/whatif", json={"vehicle_id": "nope", "action": "add_reserve"})
    big = {"vehicle_id": "x", "action": "hold_at_stop", "value": 99}
    too_big = client.post("/api/v1/whatif", json=big)
    assert unknown.status_code == 404
    assert too_big.status_code == 422
    fresh = make_runtime(FakeWall(), models_dir=tmp_path)
    with TestClient(create_app(fresh, background=False)) as c2:
        c2.post("/internal/sim/session", json={"session_id": "s2", "sim_time": START.isoformat(),
                                               "speed": 1.0, "state": "running"})  # fmt: skip
        tr = traffic()
        feed_rows(fresh, tr[(tr["et"] >= START) & (tr["et"] < START + timedelta(minutes=1))],
                  fresh.wall, START)  # fmt: skip
        vid = next(iter(fresh.store.vehicles))
        resp = c2.post("/api/v1/whatif", json={"vehicle_id": vid, "action": "hold_at_stop"})
    assert resp.status_code == 409 and "нет прогноза" in resp.json()["detail"]


@needs_data
def test_whatif_falls_back_to_heuristic_when_ml_down(live, monkeypatch):
    client, rt = live
    monkeypatch.setattr(rt, "ml", MlClient(DEAD_ML, timeout_s=0.2))

    res = _post(client, _predicted(rt)[0], "hold_at_stop", 2)

    assert res.model_mode == "fallback" and res.model_version == FALLBACK_VERSION
    assert res.delta_delay_s == pytest.approx(0.7 * 120, abs=0.2)
