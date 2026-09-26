"""Скорость на перегонах: типичная по истории и текущая по треку ТС.

Точка трека относится к перегону «остановка i → i+1» своего маршрута, если она ближе
``MAX_DIST_M`` к отрезку между остановками и её время попадает в плановое окно перегона
с запасом ``TIME_SLACK`` (так точка на возвратном рейсе не попадёт на встречный
перегон). Из нескольких кандидатов берётся ближайший.

* Типичная скорость перегона — медиана скоростей точек реальных ТС (``unit_id < 9e6``)
  за день истории.
* Текущая — средняя скорость ТС на его текущем перегоне за последние 5 сим-минут;
  ``speed_ratio = текущая / типичная`` (признак затора для причины CONGESTION).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from transit_core.network import route_id_of, segment_id_of
from transit_core.track import SYNTHETIC_UNIT_MIN, to_xy

MAX_DIST_M = 60.0
STOP_ZONE_M = 25.0
"""Точки ближе к остановке не входят в скорость перегона: это стоянка, а не ход."""
TIME_SLACK = timedelta(minutes=15)
CURRENT_WINDOW = timedelta(minutes=5)
MAX_KMH = 100.0
MIN_TYPICAL_KMH = 3.0
STOP_KMH = 3.0
MIN_POINTS = 5
CHUNK = 256
NS = 1_000_000_000


@dataclass(frozen=True)
class Segments:
    """Перегоны маршрута одного ТС в метрах и плановых секундах."""

    ids: np.ndarray  # segment_id
    ax: np.ndarray
    ay: np.ndarray
    bx: np.ndarray
    by: np.ndarray
    t_lo: np.ndarray  # tb_i − slack, секунды
    t_hi: np.ndarray  # tb_{i+1} + slack


def _seconds(values) -> np.ndarray:
    return np.asarray(values, dtype="datetime64[ns]").astype(np.int64) / NS


def route_segments(plan_tr: pd.DataFrame) -> Segments:
    """Перегоны внутри рейсов плана ТС (как в ``transit_core.network``)."""
    p = plan_tr.reset_index(drop=True)
    x, y = to_xy(p["lon"].to_numpy(), p["lat"].to_numpy())
    tb = _seconds(p["tb"].to_numpy())
    keys, trip = p["stop_key"].to_numpy(), p["trip"].to_numpy()
    ok = (trip[:-1] == trip[1:]) & (keys[:-1] != keys[1:])
    i = np.flatnonzero(ok)
    rid = route_id_of(int(p["tr_id"].iat[0])) if len(p) else ""
    ids = np.array([segment_id_of(rid, keys[k], keys[k + 1]) for k in i], dtype=object)
    slack = TIME_SLACK.total_seconds()
    return Segments(ids, x[i], y[i], x[i + 1], y[i + 1], tb[i] - slack, tb[i + 1] + slack)


def _dist(px, py, ax, ay, bx, by) -> np.ndarray:
    """Расстояния точек (столбец) до отрезков (строка), метры."""
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    with np.errstate(invalid="ignore", divide="ignore"):
        u = ((px[:, None] - ax) * dx + (py[:, None] - ay) * dy) / ll
    u = np.clip(np.nan_to_num(u), 0.0, 1.0)
    return np.hypot(ax + u * dx - px[:, None], ay + u * dy - py[:, None])


def running(s: Segments, idx: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Маска точек «на ходу»: привязаны к перегону и дальше ``STOP_ZONE_M`` от его концов."""
    ok = idx >= 0
    k = np.where(ok, idx, 0)
    d_a = np.hypot(x - s.ax[k], y - s.ay[k]) if len(s.ids) else np.zeros(len(idx))
    d_b = np.hypot(x - s.bx[k], y - s.by[k]) if len(s.ids) else np.zeros(len(idx))
    return ok & (d_a > STOP_ZONE_M) & (d_b > STOP_ZONE_M)


def assign(s: Segments, t_s: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Индекс перегона для каждой точки или −1 (точки отсортированы по времени).

    Перегоны идут в плановом порядке, поэтому для пачки точек кандидаты — непрерывный
    срез ``[первый с t_hi ≥ t_min, последний с t_lo ≤ t_max]``.
    """
    out = np.full(len(t_s), -1, dtype=np.int64)
    if not len(s.ids) or not len(t_s):
        return out
    for a in range(0, len(t_s), CHUNK):
        sl = slice(a, a + CHUNK)
        ts = t_s[sl]
        lo = int(np.searchsorted(s.t_hi, ts.min(), side="left"))
        hi = int(np.searchsorted(s.t_lo, ts.max(), side="right"))
        if hi <= lo:
            continue
        k = slice(lo, hi)
        d = _dist(x[sl], y[sl], s.ax[k], s.ay[k], s.bx[k], s.by[k])
        d = np.where((ts[:, None] >= s.t_lo[k]) & (ts[:, None] <= s.t_hi[k]), d, np.inf)
        best = np.argmin(d, axis=1)
        hit = d[np.arange(len(best)), best] <= MAX_DIST_M
        out[sl] = np.where(hit, best + lo, -1)
    return out


def _points(track: pd.DataFrame) -> tuple[np.ndarray, ...]:
    ok = track["valid"].to_numpy(dtype=bool) & (track["speed"].to_numpy(dtype=float) <= MAX_KMH)
    t = track.loc[ok]
    x, y = to_xy(t["lon"].to_numpy(dtype=float), t["lat"].to_numpy(dtype=float))
    return _seconds(t["et"].to_numpy()), x, y, t["speed"].to_numpy(dtype=float)


def load_history(path) -> pd.DataFrame:
    """Валидные точки реальных ТС из ``traffic.csv`` (быстрее полного ``load_traffic``)."""
    cols = ["tr_id", "unit_id", "event_time", "location_valid", "lon", "lat", "speed"]
    d = pd.read_csv(path, usecols=cols, dtype={"location_valid": str})
    d = d[(d["unit_id"] < SYNTHETIC_UNIT_MIN) & d["location_valid"].str.lower().eq("true")]
    return pd.DataFrame({
        "tr_id": d["tr_id"].to_numpy(dtype=np.int64),
        "unit_id": d["unit_id"].to_numpy(dtype=np.int64),
        "et": pd.to_datetime(d["event_time"], format="ISO8601").dt.floor("s").to_numpy(),
        "lon": d["lon"].to_numpy(dtype=float),
        "lat": d["lat"].to_numpy(dtype=float),
        "speed": np.trunc(d["speed"].to_numpy(dtype=float)),
        "valid": np.ones(len(d), dtype=bool),
    })  # fmt: skip


def typical_speeds(plans: dict[int, pd.DataFrame], traffic: pd.DataFrame) -> dict[str, float]:
    """Медиана скорости реальных ТС на каждом перегоне (км/ч) по треку дня."""
    real = traffic[traffic["unit_id"] < SYNTHETIC_UNIT_MIN]
    speeds: dict[str, list[np.ndarray]] = {}
    for tr_id, track in real.groupby("tr_id"):
        plan_tr = plans.get(int(tr_id))
        if plan_tr is None:
            continue
        s = route_segments(plan_tr)
        t_s, x, y, spd = _points(track.sort_values("et"))
        idx = assign(s, t_s, x, y)
        hit = running(s, idx, x, y)
        for seg_id, v in pd.Series(spd[hit]).groupby(s.ids[idx[hit]]):
            speeds.setdefault(str(seg_id), []).append(v.to_numpy())
    out = {}
    for seg_id, parts in speeds.items():
        v = np.concatenate(parts)
        if len(v) >= MIN_POINTS:
            out[seg_id] = round(float(np.median(v)), 2)
    return out


@dataclass(frozen=True)
class CurrentSegment:
    """Где ТС сейчас и как быстро едет по этому перегону."""

    segment_id: str
    speed_kmh: float
    n_points: int


def current_segment(
    segments: Segments, track: pd.DataFrame, t: datetime, window: timedelta = CURRENT_WINDOW
) -> CurrentSegment | None:
    """Текущий перегон (по последней точке) и средняя скорость на нём за ``window``."""
    recent = track[(track["et"] <= t) & (track["et"] > t - window)]
    if recent.empty:
        return None
    t_s, x, y, spd = _points(recent)
    idx = assign(segments, t_s, x, y)
    if not len(idx) or idx[-1] < 0:
        return None
    on = (idx == idx[-1]) & running(segments, idx, x, y)
    if not on.any():
        return None  # только что встал на остановку — скорость перегона не видна
    return CurrentSegment(
        str(segments.ids[idx[-1]]), round(float(spd[on].mean()), 2), int(on.sum())
    )


def speed_ratio(current_kmh: float, typical_kmh: float | None) -> float | None:
    """Текущая скорость к типичной; ``None``, если типичной нет."""
    if typical_kmh is None or typical_kmh < MIN_TYPICAL_KMH:
        return None
    return round(current_kmh / typical_kmh, 3)


def dwell_seconds(track: pd.DataFrame, t: datetime) -> float | None:
    """Время простоя: сколько секунд ТС стоит (скорость < 3 км/ч) к моменту ``t``.

    0 — ТС едет; ``None`` — нет валидных точек.
    """
    pts = track[(track["et"] <= t) & track["valid"].astype(bool)]
    if pts.empty:
        return None
    moving = pts["speed"].to_numpy(dtype=float) >= STOP_KMH
    if moving[-1]:
        return 0.0
    et = _seconds(pts["et"].to_numpy())
    idx = np.flatnonzero(moving)
    start = et[idx[-1] + 1] if len(idx) else et[0]
    return float(et[-1] - start)
