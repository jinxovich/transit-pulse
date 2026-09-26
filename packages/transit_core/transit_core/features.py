"""Признаки прогнозной точки на момент T — единый путь для офлайна и потока.

:func:`point_features` получает план одного ТС, всю историю его трека, момент ``t`` и
целевой визит. Телеметрия с ``et > t`` отбрасывается внутри функции, факт прибытия
(``time_fact_begin``) функция не получает вовсе: план приходит из
:func:`transit_core.plan.load_plan`, где этой колонки нет. Идентификаторы (ТС, остановки)
в признаки не входят.

Группы признаков:

* **A — расписание и подсказка:** ``cur_dev``, упреждение, час, свойства цели и участка
  плана между «сейчас» и целью;
* **B — GPS:** свежесть, скорости по окнам, стоянка, доля невалидных точек, положение
  относительно нитки маршрута (позиционное отклонение от графика и его тренд);
* **C — ETA:** остаток пути по маршруту и ожидаемое отклонение при текущей скорости.

Трек должен быть отсортирован по ``et``. Работает на numpy-представлениях без копий кадров.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from transit_core.track import to_xy

FEATURES: list[str] = [
    # A
    "cur_dev",
    "lead",
    "hour_sin",
    "hour_cos",
    "tgt_manual",
    "tgt_newtrip",
    "tgt_gap",
    "n_between",
    "trip_break_between",
    "max_gap_between",
    "plan_run",
    "since_last_plan",
    "trip_progress",
    # B
    "stale",
    "stale_valid",
    "invalid15",
    "spd_last",
    "spd1",
    "spd5",
    "spd15",
    "stop5",
    "dwell",
    "gps_dev",
    "gps_dist",
    "gps_dev_trend",
    "dist_tgt",
    # C
    "remain_m",
    "eta_dev",
]

NS = 1_000_000_000
STOP_KMH = 3.0
MAX_KMH = 100.0
MIN_EXPECTED_KMH = 5.0
ROUTE_WINDOW_S = 40 * 60
TREND_LAG_S = 5 * 60
# Штраф за «далёкое по времени» место на нитке: снимает неоднозначность на конечных/петлях.
DEV_PENALTY_M_PER_S = 0.5
_NAN = float("nan")


def _seconds(values: np.ndarray) -> np.ndarray:
    """datetime64[ns] → секунды эпохи (float)."""
    return values.astype("datetime64[ns]").astype(np.int64) / NS


def _plan_features(plan_tr: pd.DataFrame, tb: np.ndarray, t_s: float, ti: int) -> dict:
    """Группа A без ``cur_dev``: свойства цели и участка плана (t, цель]."""
    gap = plan_tr["gap_min"].to_numpy(dtype=float)
    new_trip = plan_tr["new_trip"].to_numpy()
    trip = plan_tr["trip"].to_numpy()
    lo = int(np.searchsorted(tb, t_s, side="right"))
    seg = slice(lo, ti + 1)
    n_between = max(ti + 1 - lo, 0)
    hour = (t_s % 86_400) / 3600.0
    t_first = int(np.searchsorted(trip, trip[ti], side="left"))
    t_last = int(np.searchsorted(trip, trip[ti], side="right")) - 1
    gaps_between = gap[seg]
    return {
        "lead": (tb[ti] - t_s) / 60.0,
        "hour_sin": float(np.sin(2 * np.pi * hour / 24)),
        "hour_cos": float(np.cos(2 * np.pi * hour / 24)),
        "tgt_manual": float(plan_tr["manual_fill"].iat[ti]),
        "tgt_newtrip": float(new_trip[ti]),
        "tgt_gap": float(gap[ti]),
        "n_between": float(n_between),
        "trip_break_between": float(new_trip[seg].max()) if n_between else 0.0,
        "max_gap_between": float(np.nanmax(gaps_between))
        if np.isfinite(gaps_between).any()
        else 0.0,
        "plan_run": (tb[ti] - tb[lo - 1]) / 60.0 if lo > 0 else _NAN,
        "since_last_plan": (t_s - tb[lo - 1]) / 60.0 if lo > 0 else _NAN,
        "trip_progress": (ti - t_first) / max(t_last - t_first, 1),
    }


def _window_mean(et_s: np.ndarray, vals: np.ndarray, t_s: float, width_s: float) -> float:
    """Среднее значений за последние ``width_s`` секунд (NaN, если точек нет)."""
    a = int(np.searchsorted(et_s, t_s - width_s, side="right"))
    return float(vals[a:].mean()) if len(vals) > a else _NAN


def _speed_features(et_s: np.ndarray, spd: np.ndarray, t_s: float) -> dict:
    """Скорости по окнам, доля стоянки и длительность текущей стоянки (валидные точки)."""
    moving = spd >= STOP_KMH
    moving_idx = np.flatnonzero(moving)
    if len(spd) == 0:
        dwell = _NAN
    elif not moving[-1] and len(moving_idx):
        dwell = float(et_s[-1] - et_s[moving_idx[-1] + 1])
    else:
        dwell = 0.0 if moving[-1] else float(et_s[-1] - et_s[0])
    return {
        "spd_last": float(spd[-1]) if len(spd) else _NAN,
        "spd1": _window_mean(et_s, spd, t_s, 60),
        "spd5": _window_mean(et_s, spd, t_s, 300),
        "spd15": _window_mean(et_s, spd, t_s, 900),
        "stop5": _window_mean(et_s, (~moving).astype(float), t_s, 300),
        "dwell": dwell,
    }


class _Route:
    """Нитка маршрута ТС: координаты остановок в метрах и накопленный путь."""

    def __init__(self, plan_tr: pd.DataFrame, tb: np.ndarray) -> None:
        self.tb = tb
        self.x, self.y = to_xy(plan_tr["lon"].to_numpy(), plan_tr["lat"].to_numpy())
        seg = np.hypot(np.diff(self.x), np.diff(self.y))
        self.seg = seg
        self.cum = np.concatenate([[0.0], np.cumsum(seg)])

    def locate(self, px: float, py: float, t_s: float) -> tuple[float, float, float] | None:
        """Проекция точки на нитку в окне ±40 мин плана: (план. время, дистанция, путь, м)."""
        a, b = np.searchsorted(self.tb, [t_s - ROUTE_WINDOW_S, t_s + ROUTE_WINDOW_S])
        a, b = int(a), int(min(b, len(self.tb) - 1))
        if b <= a:
            return None
        ax, ay = self.x[a:b], self.y[a:b]
        dx, dy = self.x[a + 1 : b + 1] - ax, self.y[a + 1 : b + 1] - ay
        ll = dx * dx + dy * dy
        with np.errstate(invalid="ignore", divide="ignore"):
            u = np.clip(np.where(ll > 0, ((px - ax) * dx + (py - ay) * dy) / ll, 0.0), 0, 1)
        dist = np.hypot(ax + u * dx - px, ay + u * dy - py)
        planned = self.tb[a:b] + u * (self.tb[a + 1 : b + 1] - self.tb[a:b])
        j = int(np.argmin(dist + DEV_PENALTY_M_PER_S * np.abs(t_s - planned)))
        k = a + j
        return float(planned[j]), float(dist[j]), float(self.cum[k] + u[j] * self.seg[k])


def _route_features(route: _Route, ti: int, et_s, x, y, spd15: float, t_s: float) -> dict:
    """Группы B (положение на нитке) и C (остаток пути, ETA)."""
    out = dict.fromkeys(
        ["gps_dev", "gps_dist", "gps_dev_trend", "dist_tgt", "remain_m", "eta_dev"], _NAN
    )
    if len(et_s) == 0:
        return out
    out["dist_tgt"] = float(np.hypot(route.x[ti] - x[-1], route.y[ti] - y[-1]))
    now = route.locate(x[-1], y[-1], t_s)
    if now is None:
        return out
    planned, dist, pos = now
    out["gps_dev"] = t_s - planned
    out["gps_dist"] = dist
    remain = route.cum[ti] - pos
    out["remain_m"] = float(remain)
    speed = max(spd15 if np.isfinite(spd15) else 0.0, MIN_EXPECTED_KMH) / 3.6
    out["eta_dev"] = float(remain / speed - (route.tb[ti] - t_s))
    j = int(np.searchsorted(et_s, t_s - TREND_LAG_S, side="right")) - 1
    if j >= 0:
        past = route.locate(x[j], y[j], et_s[j])
        if past is not None:
            out["gps_dev_trend"] = out["gps_dev"] - (et_s[j] - past[0])
    return out


def _track_slice(track: pd.DataFrame, t64: np.datetime64) -> tuple[np.ndarray, ...]:
    """История трека до ``t`` за последние 45 мин: (et_s всех, valid, et_s валидных, x, y, spd)."""
    et = track["et"].to_numpy(dtype="datetime64[ns]")
    n = int(np.searchsorted(et, t64, side="right"))
    a = int(np.searchsorted(et, t64 - np.timedelta64(ROUTE_WINDOW_S, "s"), side="right"))
    a = min(a, max(n - 1, 0))
    et_s = _seconds(et[a:n])
    valid = track["valid"].to_numpy(dtype=bool)[a:n]
    lon, lat = track["lon"].to_numpy(dtype=float)[a:n], track["lat"].to_numpy(dtype=float)[a:n]
    x, y = to_xy(lon[valid], lat[valid])
    spd = np.clip(track["speed"].to_numpy(dtype=float)[a:n][valid], 0, MAX_KMH)
    return et_s, valid, et_s[valid], x, y, spd


def point_features(
    plan_tr: pd.DataFrame, track: pd.DataFrame, t: datetime, visit_id: int, cur_dev_s: float | None
) -> dict[str, float]:
    """Признаки прогнозной точки ``(ТС, t)`` для целевого визита ``visit_id``.

    :param plan_tr: план одного ТС из :func:`transit_core.plan.load_plan` (без факта).
    :param track: вся история трека ТС (``et, lon, lat, speed, heading, valid``),
        отсортирована по ``et``; используются только точки с ``et <= t``.
    :param t: момент прогноза (naive, время датасета).
    :param visit_id: ``tt_action_item_id`` целевого визита.
    :param cur_dev_s: подсказка (или онлайн-оценка) текущего отклонения, сек; None → NaN.
    :return: словарь с ключами :data:`FEATURES`; пропуски — NaN.
    """
    t64 = np.datetime64(t, "ns")
    t_s = float(t64.astype(np.int64)) / NS
    tb = _seconds(plan_tr["tb"].to_numpy(dtype="datetime64[ns]"))
    hits = np.flatnonzero(plan_tr["visit_id"].to_numpy() == visit_id)
    if len(hits) == 0:
        raise KeyError(f"визит {visit_id} не найден в плане ТС")
    ti = int(hits[0])
    f = {"cur_dev": _NAN if cur_dev_s is None else float(cur_dev_s)}
    f.update(_plan_features(plan_tr, tb, t_s, ti))
    et_s, valid, vet_s, x, y, spd = _track_slice(track, t64)
    f["stale"] = t_s - et_s[-1] if len(et_s) else _NAN
    f["stale_valid"] = t_s - vet_s[-1] if len(vet_s) else _NAN
    f["invalid15"] = _window_mean(et_s, (~valid).astype(float), t_s, 900)
    f.update(_speed_features(vet_s, spd, t_s))
    f.update(_route_features(_Route(plan_tr, tb), ti, vet_s, x, y, f["spd15"], t_s))
    return {k: float(f[k]) for k in FEATURES}
