"""Сим-часы, сессии и режимы деградации (время инжектится, без sleep)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from backend_kit import FakeWall, has_raw, make_runtime, nav_frame, session

from services.backend.app.degrade import ModeInputs, compute_mode
from services.backend.app.ingest.codec import FrameDecoder
from services.backend.app.ingest.worker import apply_frame
from services.backend.app.state.clock import SessionUpdate, SimClock

T0 = datetime(2026, 1, 6, 7, 0, 0)
needs_data = pytest.mark.skipif(not has_raw(), reason="нет data/raw")
UNIT, TR = 663271, 116445  # борт из validate (no_schedule достаточно для state)


def test_clock_ticks_by_formula_while_running():
    clock = SimClock()
    clock.apply(SessionUpdate("s1", T0, 30.0, "running"), wall=100.0)
    assert clock.now(100.0) == T0
    assert clock.now(102.0) == T0 + timedelta(seconds=60)


def test_clock_stops_on_pause_and_never_rewinds_within_session():
    clock = SimClock()
    clock.apply(SessionUpdate("s1", T0, 10.0, "running"), wall=0.0)
    clock.apply(SessionUpdate("s1", T0 + timedelta(seconds=5), 10.0, "paused"), wall=1.0)
    assert clock.now(1.0) == T0 + timedelta(seconds=10)  # replayer отстал — не откатываемся
    assert clock.now(50.0) == T0 + timedelta(seconds=10)  # пауза: часы стоят


def test_new_session_resets_clock_even_backwards():
    clock = SimClock()
    clock.apply(SessionUpdate("s1", T0, 10.0, "running"), wall=0.0)
    assert clock.apply(SessionUpdate("s2", T0 - timedelta(hours=1), 5.0, "running"), wall=10.0)
    assert clock.now(10.0) == T0 - timedelta(hours=1)
    assert not clock.apply(SessionUpdate("s2", T0 - timedelta(hours=1), 5.0, "running"), 10.0)


def test_packets_only_pull_watermark_forward():
    clock = SimClock()
    clock.apply(SessionUpdate("s1", T0, 1.0, "running"), wall=0.0)
    clock.observe(T0 + timedelta(seconds=30), wall=0.0)
    assert clock.now(0.0) == T0 + timedelta(seconds=30)
    clock.observe(T0, wall=0.0)
    assert clock.now(0.0) == T0 + timedelta(seconds=30)
    assert not clock.accepts(T0 + timedelta(hours=3), wall=0.0)  # хвост чужой сессии


def _inputs(wall, last=None, session_wall=0.0, warming=False) -> ModeInputs:
    return ModeInputs(wall, last, session_wall, warming, 30.0)


def test_modes_offline_paused_live_degraded_and_back():
    clock = SimClock()
    assert compute_mode(clock, _inputs(0.0))[0] == "OFFLINE"
    clock.apply(SessionUpdate("s1", T0, 30.0, "running"), wall=0.0)
    assert compute_mode(clock, _inputs(5.0, last=4.0))[0] == "LIVE"
    mode, reason = compute_mode(clock, _inputs(40.0, last=9.0))
    assert mode == "DEGRADED" and "31" in reason
    assert compute_mode(clock, _inputs(41.0, last=41.0))[0] == "LIVE"
    assert compute_mode(clock, _inputs(41.0, last=41.0, warming=True))[0] == "WARMING_UP"
    clock.apply(SessionUpdate("s1", T0, 30.0, "paused"), wall=100.0)
    assert compute_mode(clock, _inputs(500.0, last=41.0))[0] == "PAUSED"  # пауза ≠ обрыв


def test_session_without_packets_degrades_after_30s():
    clock = SimClock()
    clock.apply(SessionUpdate("s1", T0, 30.0, "running"), wall=0.0)
    assert compute_mode(clock, _inputs(29.0, session_wall=0.0))[0] == "LIVE"
    assert compute_mode(clock, _inputs(30.0, session_wall=0.0))[0] == "DEGRADED"


@needs_data
def test_new_session_clears_state_and_incidents():
    wall = FakeWall()
    rt = make_runtime(wall)
    session(rt, "s1", T0)
    frame = FrameDecoder().feed(nav_frame(UNIT, T0))[0]
    assert apply_frame(rt, wall(), frame)
    assert str(TR) in rt.store.vehicles
    events = []
    rt.listeners.append(lambda kind, payload: events.append((kind, payload)))
    assert session(rt, "s2", T0 + timedelta(hours=2))
    assert rt.store.vehicles == {} and rt.book.incidents == {}
    assert events == [("session", True)]
    assert not session(rt, "s2", T0 + timedelta(hours=2), speed=60.0)  # только часы
    assert rt.clock.speed == 60.0


@needs_data
def test_degraded_by_wall_clock_marks_vehicles_stale_and_recovers():
    wall = FakeWall()
    rt = make_runtime(wall)
    session(rt, "s1", T0, speed=30.0)
    dec = FrameDecoder()
    frame = dec.feed(nav_frame(UNIT, T0))[0]
    rt.stats.on_frame(frame, wall())
    apply_frame(rt, wall(), frame)
    assert rt.mode()[0] == "LIVE"
    wall.advance(31.0)
    assert rt.mode()[0] == "DEGRADED"
    assert rt.vehicle_states()[str(TR)].stale
    et = rt.sim_now()
    frame = dec.feed(nav_frame(UNIT, et))[0]
    rt.stats.on_frame(frame, wall())
    apply_frame(rt, wall(), frame)
    assert rt.mode()[0] == "LIVE"
    assert not rt.vehicle_states()[str(TR)].stale


@needs_data
def test_unknown_unit_and_out_of_day_timestamp_do_not_move_clock():
    wall = FakeWall()
    rt = make_runtime(wall)
    session(rt, "s1", T0, state="paused")
    dec = FrameDecoder()
    for raw in (nav_frame(990001, T0 + timedelta(hours=1)),
                nav_frame(UNIT, datetime(2026, 9, 27, 12, 0))):  # fmt: skip
        assert apply_frame(rt, wall(), dec.feed(raw)[0])
    assert rt.sim_now() == T0
    states = rt.vehicle_states()
    assert states["u:990001"].kind == "unknown" and states["u:990001"].prediction is None
    assert states[f"u:{UNIT}"].kind == "unknown"
    assert rt.stats.unknown_units == {990001, UNIT}


@needs_data
def test_auto_session_when_packets_arrive_without_replayer():
    wall = FakeWall()
    rt = make_runtime(wall)
    frame = FrameDecoder().feed(nav_frame(UNIT, T0))[0]
    apply_frame(rt, wall(), frame)
    assert rt.clock.session_id == "auto-0001"
    assert rt.sim_now() == T0
