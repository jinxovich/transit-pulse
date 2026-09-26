"""Map matching: монотонный прогресс, фильтры NDTP, курс, смена рейса, отклонение от графика."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from transit_core.matching import RESYNC_N, RouteMatcher, schedule_deviation_s, to_seconds
from transit_core.route_line import build_lines
from transit_core.track import M_PER_DEG_LAT, M_PER_DEG_LON

T0 = datetime(2026, 1, 6, 8, 0, 0)
LON0, LAT0 = 37.6, 55.75
EAST, NORTH, WEST = 90.0, 0.0, 270.0


def _lonlat(x: float, y: float) -> tuple[float, float]:
    return LON0 + x / M_PER_DEG_LON, LAT0 + y / M_PER_DEG_LAT


def _plan(stops: list[tuple[float, float, float, int]]) -> pd.DataFrame:
    """План из остановок ``(x, y, секунды от T0, trip)`` в метрах."""
    rows = []
    for i, (x, y, dt, trip) in enumerate(stops):
        lon, lat = _lonlat(x, y)
        rows.append({"visit_id": 100 + i, "tr_id": 1, "tb": T0 + timedelta(seconds=dt),
                     "lon": lon, "lat": lat, "stop_key": f"st_{i}", "trip": trip})  # fmt: skip
    df = pd.DataFrame(rows)
    df["tb"] = df["tb"].astype("datetime64[ns]")
    return df


def _feed(m: RouteMatcher, t: float, x: float, y: float, speed: float = 36.0, course=EAST):
    lon, lat = _lonlat(x, y)
    return m.update(T0 + timedelta(seconds=t), lon, lat, speed, course, True)


@pytest.fixture
def l_line() -> pd.DataFrame:
    """L-образная нитка: 1000 м на восток, затем 1000 м на север, остановка каждые 500 м."""
    pts = [(0, 0), (500, 0), (1000, 0), (1000, 500), (1000, 1000)]
    return _plan([(x, y, 50.0 * i, 1) for i, (x, y) in enumerate(pts)])


def _drive_east(m: RouteMatcher, until_x: float, step: float = 100.0):
    """Едем на восток по нижней части L с поперечным шумом ±5 м (10 м/с)."""
    out, x, t = [], 0.0, 0.0
    while x <= until_x:
        out.append(_feed(m, t, x, 5.0 * (-1) ** int(x / step)))
        x, t = x + step, t + step / 10.0
    return out, t


def test_progress_monotonic_on_l_route(l_line):
    m = RouteMatcher(l_line)
    res, t = _drive_east(m, 1000.0)
    for y in range(100, 1001, 100):
        t += 10.0
        res.append(_feed(m, t, 1000.0 + 4.0, float(y), course=NORTH))
    prog = [r.progress_m for r in res]
    assert all(r is not None and r.on_route for r in res)
    assert np.all(np.diff(prog) >= 0)
    assert prog[-1] == pytest.approx(2000.0, abs=10.0)
    assert max(r.offset_m for r in res) < 10.0
    assert res[-1].next_stop is None and res[5].segment_idx == 1


def test_gps_outlier_is_dropped(l_line):
    m = RouteMatcher(l_line)
    res, t = _drive_east(m, 300.0)
    before = res[-1].progress_m
    assert _feed(m, t, 300.0, 2500.0) is None  # 2.2 км за 10 с
    assert m.stats["outlier"] == 1
    nxt = _feed(m, t + 10.0, 400.0, 0.0)
    assert nxt.progress_m == pytest.approx(400.0, abs=5.0) and nxt.progress_m >= before


def test_backward_jump_does_not_reduce_progress(l_line):
    m = RouteMatcher(l_line)
    res, t = _drive_east(m, 700.0)
    back = _feed(m, t, 520.0, 3.0, speed=20.0, course=WEST)
    assert back.progress_m == pytest.approx(res[-1].progress_m)


def test_standing_does_not_move_progress(l_line):
    m = RouteMatcher(l_line)
    res, t = _drive_east(m, 500.0)
    base = res[-1].progress_m
    for i, (dx, dy) in enumerate([(8, 3), (-6, 9), (12, -7), (-10, -4)]):
        r = _feed(m, t + 10.0 * (i + 1), 500.0 + dx, dy, speed=0.0, course=0.0)
        assert r.progress_m == base
    assert m.stats["still"] == 4


def test_invalid_points_are_skipped(l_line):
    m = RouteMatcher(l_line)
    res, t = _drive_east(m, 300.0)
    assert m.update(T0 + timedelta(seconds=t), 0.0, 0.0, 0.0, 0.0, False) is None
    lon, lat = _lonlat(900.0, 0.0)
    assert m.update(T0 + timedelta(seconds=t + 1), lon, lat, 30.0, EAST, False) is None
    assert m.update(T0 + timedelta(seconds=t + 2), 0.0, 0.0, 30.0, EAST, True) is None
    assert m.stats["invalid"] == 3
    assert m.last.progress_m == res[-1].progress_m


def test_course_resolves_u_turn_ambiguity():
    """Точка ровно между «туда» и «обратно»; время и расстояние одинаковы — решает курс."""
    pts = [(0, 0, 0), (500, 0, 60), (1000, 0, 120), (1000, 40, 125), (500, 40, 185), (0, 40, 245)]
    plan = _plan([(x, y, t, 1) for x, y, t in pts])
    t_mid = 122.5  # |t − план(500 м)| = |t − план(1540 м)|
    west = _feed(RouteMatcher(plan), t_mid, 500.0, 20.0, speed=30.0, course=WEST)
    east = _feed(RouteMatcher(plan), t_mid, 500.0, 20.0, speed=30.0, course=EAST)
    assert west.progress_m == pytest.approx(1540.0, abs=1.0)
    assert east.progress_m == pytest.approx(500.0, abs=1.0)
    assert west.prev_stop == 4 and east.prev_stop == 1


def test_trip_switch_at_terminal():
    """Прибыли на конечную рейса 1, отстояли, уехали обратно — рейс 2."""
    plan = _plan([(0, 0, 0, 1), (500, 0, 60, 1), (1000, 0, 120, 1),
                  (1000, 0, 600, 2), (500, 0, 660, 2), (0, 0, 720, 2)])  # fmt: skip
    m = RouteMatcher(plan)
    for i, x in enumerate(range(0, 1001, 100)):
        r = _feed(m, 12.0 * i, float(x), 0.0)
    assert r.trip == 1 and r.next_stop is None
    r = _feed(m, 590.0, 1003.0, 2.0, speed=0.0)
    assert r.trip == 2 and schedule_deviation_s(r, m.line_of(2), 590.0 + to_seconds(T0)) == 0
    for i, x in enumerate(range(900, 400, -100)):
        r = _feed(m, 610.0 + 10.0 * i, float(x), 0.0, course=WEST)
    assert r.trip == 2 and r.progress_m == pytest.approx(500.0, abs=5.0)
    assert m.stats["switch"] == 1


def test_schedule_deviation_interpolates_between_stops(l_line):
    m = RouteMatcher(l_line)
    r = _feed(m, 0.0, 0.0, 0.0)
    line = m.line_of(1)
    assert schedule_deviation_s(r, line, T0 - timedelta(seconds=30)) == 0.0  # отстой
    r = _feed(m, 70.0, 750.0, 0.0)  # план 750 м — 75 с
    assert schedule_deviation_s(r, line, T0 + timedelta(seconds=70)) == pytest.approx(-5.0)
    assert m.deviation_s() == pytest.approx(-5.0)
    assert r.dist_to_next_m == pytest.approx(250.0, abs=1.0)


def test_build_lines_skips_single_stop_trips():
    plan = _plan([(0, 0, 0, 1), (100, 0, 30, 1), (100, 0, 600, 2), (0, 0, 900, 3), (50, 0, 930, 3)])
    lines = build_lines(plan)
    assert [ln.trip for ln in lines] == [1, 3]
    assert lines[1].rows.tolist() == [3, 4]


def test_out_of_order_points_do_not_trigger_resync(l_line):
    m = RouteMatcher(l_line)
    res, t = _drive_east(m, 400.0)
    for _ in range(RESYNC_N + 1):
        assert _feed(m, t - 30.0, 100.0, 0.0) is None
    assert m.stats["late"] == RESYNC_N + 1 and m.stats["resync"] == 0
    assert _feed(m, t, 500.0, 0.0).progress_m == pytest.approx(500.0, abs=5.0)
