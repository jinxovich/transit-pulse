"""Скорость на перегонах: привязка точек, типичная/текущая скорость, простой."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
from conftest import START, STOP_STEP_S, TRUE_DELAY_S

from transit_core.causes import infer_cause
from transit_core.segment_speed import (
    assign,
    current_segment,
    dwell_seconds,
    route_segments,
    speed_ratio,
    typical_speeds,
)
from transit_core.track import to_xy


def test_points_are_assigned_to_segment_between_their_stops(synth_plan, synth_track):
    segs = route_segments(synth_plan)
    assert len(segs.ids) == len(synth_plan) - 1 and segs.ids[0].startswith("r7:")
    x, y = to_xy(synth_track["lon"].to_numpy(), synth_track["lat"].to_numpy())
    t_s = synth_track["et"].to_numpy(dtype="datetime64[ns]").astype(np.int64) / 1e9
    idx = assign(segs, t_s, x, y)
    moving = (synth_track["speed"].to_numpy() > 0) & (idx >= 0)
    assert moving.sum() >= 45  # хвост за последней остановкой не привязан
    assert (np.diff(idx[moving]) >= 0).all()  # монотонно вперёд по маршруту


def test_typical_and_current_speed_give_ratio_near_one(synth_plan, synth_track):
    traffic = synth_track.assign(tr_id=7, unit_id=101)
    typical = typical_speeds({7: synth_plan}, traffic)
    assert typical and all(20 <= v <= 25 for v in typical.values())
    t = START + timedelta(seconds=STOP_STEP_S * 5 + 30)
    cur = current_segment(route_segments(synth_plan), synth_track, t)
    assert cur is not None and cur.segment_id in typical
    assert 0.8 <= speed_ratio(cur.speed_kmh, typical[cur.segment_id]) <= 1.2


def test_synthetic_units_are_not_typical(synth_plan, synth_track):
    traffic = synth_track.assign(tr_id=7, unit_id=9_000_001)
    assert typical_speeds({7: synth_plan}, traffic) == {}


def test_dwell_counts_standing_time(synth_track):
    assert dwell_seconds(synth_track, START + timedelta(seconds=TRUE_DELAY_S - 10)) >= 100
    assert dwell_seconds(synth_track, START + timedelta(minutes=5)) == 0.0
    assert dwell_seconds(synth_track.iloc[0:0], START) is None


def test_speed_ratio_guards_and_congestion_cause():
    assert speed_ratio(10.0, None) is None and speed_ratio(10.0, 1.0) is None
    assert speed_ratio(6.0, 24.0) == 0.25
    cause = infer_cause({"cur_dev": 90.0, "speed_ratio": 0.3, "seg_speed": 5.0, "dwell": 0.0},
                        predicted_delay_s=180)  # fmt: skip
    assert cause.code == "CONGESTION"
    shown = {e.feature: e.value for e in cause.evidence}
    assert shown["seg_speed"] == "5.0 км/ч" and shown["speed_ratio"] == "0.30"
    assert shown["dwell"] == "0 с"
