"""Последовательности телеметрии для GRU-модели.

Модуль превращает трек одного ТС в тензор фиксированной формы на момент прогноза ``t``:
последние 20 минут на сетке 15 с (80 шагов). Плюс компактные статические признаки
группы A (подсказка ``cur_dev``, упреждение, час, свойства целевой остановки в плане).

Правило честности: в расчёт идут только строки трека с ``et <= t``; плановое расписание
передаётся уже без факта (``prepare_plan`` читает только плановые колонки).
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

WINDOW_MIN = 20
STEP_S = 15
N_STEPS = WINDOW_MIN * 60 // STEP_S
SEQ_FEATURES = (
    "speed", "is_stop", "valid_share", "no_data", "dx", "dy", "stop_dist", "gps_dev",
)
N_SEQ_FEATURES = len(SEQ_FEATURES)
STATIC_FEATURES = (
    "cur_dev", "lead", "hour_sin", "hour_cos", "tgt_manual", "tgt_new_trip", "tgt_gap",
    "tgt_gap_missing", "trip_break_between", "max_gap_between", "n_between", "dist_tgt",
    "stale",
)
N_STATIC_FEATURES = len(STATIC_FEATURES)

PLAN_WINDOW = pd.Timedelta("40min")
TRIP_GAP_MIN = 5.0
STOP_SPEED_KMH = 3.0
MAX_SPEED_KMH = 150.0
M_PER_DEG_LAT = 111_195.0
M_PER_DEG_LON = M_PER_DEG_LAT * float(np.cos(np.radians(55.75)))

# Масштабы нормировки: величины приводятся примерно к [-3, 3].
SPEED_SCALE = 40.0
DXY_SCALE = 100.0
DXY_CLIP = 10.0
DIST_LOG_SCALE = 7.0
DEV_CLIP_S = 1800.0
DEV_SCALE_S = 600.0
CUR_DEV_SCALE = 300.0
LEAD_SCALE_MIN = 15.0
GAP_CLIP_MIN = 120.0
GAP_SCALE_MIN = 60.0
N_BETWEEN_SCALE = 20.0
STALE_CLIP_S = 1800.0
STALE_SCALE_S = 300.0

PLAN_COLS = ["tt_action_item_id", "tr_id", "time_begin", "manual_fill", "geom"]


def prepare_plan(schedule: pd.DataFrame) -> pd.DataFrame:
    """Готовит плановое расписание: время, координаты остановки, разрывы рейсов.

    Берутся только плановые колонки ``PLAN_COLS`` — факт прибытия сюда не попадает.
    """
    s = schedule[PLAN_COLS].copy()
    s["tb"] = pd.to_datetime(s["time_begin"], format="mixed")
    xy = s["geom"].str.extract(r"POINT \(([\d.]+) ([\d.]+)\)").astype(float)
    s["slon"], s["slat"] = xy[0], xy[1]
    s["manual_fill"] = s["manual_fill"].astype(str).str.lower().eq("true").astype(int)
    s = s.sort_values(["tr_id", "tb"], kind="stable")
    s["gap"] = s.groupby("tr_id")["tb"].diff().dt.total_seconds() / 60
    s["new_trip"] = (s["gap"].isna() | (s["gap"] >= TRIP_GAP_MIN)).astype(int)
    cols = ["tt_action_item_id", "tr_id", "tb", "slon", "slat", "gap", "new_trip", "manual_fill"]
    return s[cols].reset_index(drop=True)


def _meters(lon: np.ndarray, lat: np.ndarray, lon0: float, lat0: float):
    return (lon - lon0) * M_PER_DEG_LON, (lat - lat0) * M_PER_DEG_LAT


def _history(track: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    """Строки трека строго с ``et <= t`` в порядке времени."""
    hist = track.loc[track["et"] <= t]
    return hist.sort_values("et", kind="stable")


def _valid_mask(hist: pd.DataFrame) -> np.ndarray:
    valid = hist["valid"].to_numpy(dtype=bool)
    lon, lat, spd = (hist[c].to_numpy(dtype=float) for c in ("lon", "lat", "speed"))
    ok = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(spd) & (spd <= MAX_SPEED_KMH)
    return valid & ok


def _last_per_bin(bins: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Последнее по времени значение в каждой ячейке сетки (строки уже по времени)."""
    rev_bins = bins[::-1]
    uniq, first_in_rev = np.unique(rev_bins, return_index=True)
    return uniq, values[::-1][first_in_rev]


def _grid_positions(hist, valid, bins, in_win) -> tuple[np.ndarray, np.ndarray]:
    """Позиция ТС на конец каждого шага (прямое заполнение, NaN до первой точки)."""
    lon = hist["lon"].to_numpy(dtype=float)
    lat = hist["lat"].to_numpy(dtype=float)
    pos = np.full((N_STEPS + 1, 2), np.nan)  # [0] — последняя точка до окна
    before = valid & ~in_win
    if before.any():
        j = np.flatnonzero(before)[-1]
        pos[0] = lon[j], lat[j]
    sel = valid & in_win
    if sel.any():
        uniq, idx = _last_per_bin(bins[sel], np.flatnonzero(sel))
        pos[uniq + 1, 0], pos[uniq + 1, 1] = lon[idx], lat[idx]
    pos = pd.DataFrame(pos).ffill().to_numpy()
    return pos[:, 0], pos[:, 1]


def _plan_features(plan_tr, t, lon, lat, out) -> None:
    """Дистанция до ближайшей плановой остановки и GPS-отклонение от нитки."""
    near = plan_tr[(plan_tr["tb"] >= t - PLAN_WINDOW) & (plan_tr["tb"] <= t + PLAN_WINDOW)]
    has_pos = np.isfinite(lon)
    if near.empty or not has_pos.any():
        return
    slon, slat = near["slon"].to_numpy(float), near["slat"].to_numpy(float)
    dx, dy = _meters(lon[has_pos, None], lat[has_pos, None], slon[None, :], slat[None, :])
    dist = np.hypot(dx, dy)
    j = dist.argmin(axis=1)
    step_end = t - pd.to_timedelta((N_STEPS - 1 - np.arange(N_STEPS)) * STEP_S, unit="s")
    tb = near["tb"].to_numpy("datetime64[ns]")[j]
    dev = (step_end.to_numpy("datetime64[ns]")[has_pos] - tb) / np.timedelta64(1, "s")
    out[has_pos, 6] = np.log1p(dist[np.arange(len(j)), j]) / DIST_LOG_SCALE
    out[has_pos, 7] = np.clip(dev, -DEV_CLIP_S, DEV_CLIP_S) / DEV_SCALE_S


def build_sequence(
    track: pd.DataFrame, t: datetime, plan_tr: pd.DataFrame | None = None
) -> np.ndarray:
    """Последовательность ``(N_STEPS, N_SEQ_FEATURES)`` за 20 минут до ``t``.

    ``track`` — колонки ``et, lon, lat, speed, heading, valid`` одного ТС. Шаг ``k``
    покрывает интервал ``(t - (N_STEPS-k)·15 с, t - (N_STEPS-1-k)·15 с]``. Признаки шага:
    скорость, стоянка, доля валидных строк, «нет данных», смещение по x/y в метрах,
    дистанция до ближайшей плановой остановки и отклонение от нитки (если есть план).
    """
    t = pd.Timestamp(t)
    out = np.zeros((N_STEPS, N_SEQ_FEATURES), dtype=np.float32)
    hist = _history(track, t)
    sec = (t - hist["et"]).dt.total_seconds().to_numpy()
    bins = (N_STEPS - 1 - np.floor(sec / STEP_S)).astype(int)
    in_win = bins >= 0
    valid = _valid_mask(hist)

    cnt = np.bincount(bins[in_win], minlength=N_STEPS)
    vsel = in_win & valid
    vcnt = np.bincount(bins[vsel], minlength=N_STEPS)
    spd = np.nan_to_num(hist["speed"].to_numpy(dtype=float))
    ssum = np.bincount(bins[vsel], weights=spd[vsel], minlength=N_STEPS)
    mean_spd = np.divide(ssum, vcnt, out=np.zeros(N_STEPS), where=vcnt > 0)
    out[:, 0] = mean_spd / SPEED_SCALE
    out[:, 1] = ((vcnt > 0) & (mean_spd < STOP_SPEED_KMH)).astype(np.float32)
    out[:, 2] = np.divide(vcnt, cnt, out=np.zeros(N_STEPS), where=cnt > 0)
    out[:, 3] = (cnt == 0).astype(np.float32)

    lon, lat = _grid_positions(hist, valid, bins, in_win)
    dx, dy = np.diff(lon) * M_PER_DEG_LON, np.diff(lat) * M_PER_DEG_LAT
    out[:, 4] = np.clip(np.nan_to_num(dx) / DXY_SCALE, -DXY_CLIP, DXY_CLIP)
    out[:, 5] = np.clip(np.nan_to_num(dy) / DXY_SCALE, -DXY_CLIP, DXY_CLIP)
    if plan_tr is not None:
        _plan_features(plan_tr, t, lon[1:], lat[1:], out)
    return out


def _last_position(track: pd.DataFrame | None, t: pd.Timestamp):
    """Последняя валидная позиция и её время на момент ``t`` (или ``None``)."""
    if track is None:
        return None
    hist = _history(track, t)
    valid = _valid_mask(hist)
    if not valid.any():
        return None
    row = hist.iloc[np.flatnonzero(valid)[-1]]
    return float(row["lon"]), float(row["lat"]), pd.Timestamp(row["et"])


def sequence_static(
    t: datetime,
    target_stop_id: int,
    target_time: datetime,
    cur_dev_s: float,
    plan_tr: pd.DataFrame,
    track: pd.DataFrame | None = None,
) -> np.ndarray:
    """Статические признаки группы A для точки прогноза (только данные на момент ``t``).

    ``plan_tr`` — результат ``prepare_plan`` для одного ТС; ``track`` нужен лишь для
    дистанции до цели и «свежести» последней точки.
    """
    t, target_time = pd.Timestamp(t), pd.Timestamp(target_time)
    f = np.zeros(N_STATIC_FEATURES, dtype=np.float32)
    hour = t.hour + t.minute / 60
    f[0] = cur_dev_s / CUR_DEV_SCALE
    f[1] = (target_time - t).total_seconds() / 60 / LEAD_SCALE_MIN
    f[2], f[3] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    tgt = plan_tr[plan_tr["tt_action_item_id"] == target_stop_id]
    if not tgt.empty:
        row = tgt.iloc[0]
        f[4], f[5] = row["manual_fill"], row["new_trip"]
        gap_missing = not np.isfinite(row["gap"])
        f[6] = 0.0 if gap_missing else min(row["gap"], GAP_CLIP_MIN) / GAP_SCALE_MIN
        f[7] = float(gap_missing)
    between = plan_tr[(plan_tr["tb"] > t) & (plan_tr["tb"] <= target_time)]
    if len(between):
        f[8] = between["new_trip"].max()
        f[9] = min(np.nan_to_num(between["gap"].max()), GAP_CLIP_MIN) / GAP_SCALE_MIN
        f[10] = len(between) / N_BETWEEN_SCALE
    last = _last_position(track, t)
    if last is not None and not tgt.empty:
        dx, dy = _meters(last[0], last[1], tgt.iloc[0]["slon"], tgt.iloc[0]["slat"])
        f[11] = np.log1p(np.hypot(dx, dy)) / DIST_LOG_SCALE
    stale = STALE_CLIP_S if last is None else (t - last[2]).total_seconds()
    f[12] = min(stale, STALE_CLIP_S) / STALE_SCALE_S
    return f
