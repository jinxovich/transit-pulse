"""Виртуальный факт прибытия на остановку по GPS.

Прибытие — момент входа в круг радиуса :data:`ENTER_RADIUS_M` вокруг остановки (с линейной
интерполяцией по отрезкам трека). Если в круг ТС не вошло, берётся момент наибольшего
сближения, если оно не дальше :data:`NEAR_RADIUS_M`. Для первой остановки рейса (конечная)
факт в данных — отправление, поэтому там берётся момент выезда из круга
:data:`DEPART_RADIUS_M`. Отрезки трека против направления маршрута игнорируются. Визиты
обходятся по плану, и каждый следующий ищется только после предыдущего найденного прибытия
(монотонность).

Радиус входа подобран на train (факт используется только в ``scripts/eval_detector.py``):
15 м даёт меньшую ошибку, чем 35 м, — детектор с большим радиусом систематически «спешит».

Используется только телеметрия с ``et <= until`` — функция пригодна для потока.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from transit_core.track import to_xy

ENTER_RADIUS_M = 15.0
NEAR_RADIUS_M = 80.0
STILL_M = 5.0
NEAR_LEAVE_M = 30.0
DEPART_RADIUS_M = 35.0
WINDOW_BEFORE_S = 12 * 60
WINDOW_AFTER_S = 20 * 60
ONLINE_LOOKBACK_S = 45 * 60
NS = 1_000_000_000


def _valid_track(track: pd.DataFrame, until: np.datetime64) -> tuple[np.ndarray, ...]:
    """Валидные точки трека до ``until``: (время в секундах, x, y)."""
    et = track["et"].to_numpy(dtype="datetime64[ns]")
    mask = track["valid"].to_numpy(dtype=bool) & (et <= until)
    t_s = et[mask].astype(np.int64) / NS
    x, y = to_xy(track["lon"].to_numpy()[mask], track["lat"].to_numpy()[mask])
    return t_s, x, y


def _segment_hits(ts, x, y, sx, sy) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Для каждого отрезка трека: доля u ближайшей точки, мин. дистанция, начало, вектор."""
    ax, ay = x[:-1] - sx, y[:-1] - sy
    dx, dy = np.diff(x), np.diff(y)
    ll = dx * dx + dy * dy
    with np.errstate(invalid="ignore", divide="ignore"):
        u = np.where(ll > 0, -(ax * dx + ay * dy) / ll, 0.0)
    u = np.clip(u, 0.0, 1.0)
    dmin = np.hypot(ax + u * dx, ay + u * dy)
    return u, dmin, np.stack([ax, ay]), np.stack([dx, dy])


def _entry_fraction(a: np.ndarray, d: np.ndarray, u_min: float, radius: float) -> float:
    """Наименьшая доля отрезка, на которой расстояние до остановки равно ``radius``."""
    ax, ay = a
    dx, dy = d
    if ax * ax + ay * ay <= radius * radius:
        return 0.0
    qa = dx * dx + dy * dy
    qb = 2 * (ax * dx + ay * dy)
    qc = ax * ax + ay * ay - radius * radius
    disc = qb * qb - 4 * qa * qc
    if qa <= 0 or disc < 0:
        return u_min
    return float(np.clip((-qb - np.sqrt(disc)) / (2 * qa), 0.0, u_min))


def _departure_time(ts, x, y, sx, sy) -> float | None:
    """Момент выезда из круга :data:`ENTER_RADIUS_M` после первого входа в него.

    Для первой остановки рейса (конечной) факт — отправление, а не прибытие.
    """
    dist = np.hypot(x - sx, y - sy)
    inside = np.flatnonzero(dist <= DEPART_RADIUS_M)
    if len(inside) == 0:
        return None
    after = np.flatnonzero(dist[inside[0] :] > DEPART_RADIUS_M)
    if len(after) == 0:
        return None
    i = int(inside[0] + after[0])
    d0, d1 = dist[i - 1], dist[i]
    frac = (DEPART_RADIUS_M - d0) / (d1 - d0) if d1 > d0 else 0.0
    return float(ts[i - 1] + frac * (ts[i] - ts[i - 1]))


def _arrival_time(ts, x, y, sx, sy, direction: np.ndarray) -> float | None:
    """Время прибытия (секунды эпохи) по окну трека или None.

    Отрезки, где ТС движется против направления маршрута (``direction``), не учитываются —
    так отсекаются проезды мимо остановки встречного направления.
    """
    if len(ts) == 0:
        return None
    if len(ts) == 1:
        d = float(np.hypot(x[0] - sx, y[0] - sy))
        return float(ts[0]) if d <= NEAR_RADIUS_M else None
    u, dmin, a, d = _segment_hits(ts, x, y, sx, sy)
    seg_len = np.hypot(d[0], d[1])
    against = (d[0] * direction[0] + d[1] * direction[1] < 0) & (seg_len > STILL_M)
    dmin = np.where(against, np.inf, dmin)
    inside = np.flatnonzero(dmin <= ENTER_RADIUS_M)
    if len(inside):
        j = int(inside[0])
        uj = _entry_fraction(a[:, j], d[:, j], float(u[j]), ENTER_RADIUS_M)
    else:
        j = int(np.argmin(dmin))
        last_d = float(np.hypot(x[-1] - sx, y[-1] - sy))
        if dmin[j] > NEAR_RADIUS_M or last_d < dmin[j] + NEAR_LEAVE_M:
            return None  # ещё не проехали мимо: минимум может быть впереди
        uj = float(u[j])
    return float(ts[j] + uj * (ts[j + 1] - ts[j]))


def _route_directions(px: np.ndarray, py: np.ndarray) -> np.ndarray:
    """Направление маршрута у каждой остановки: от предыдущей к следующей (2×n)."""
    if len(px) < 2:
        return np.zeros((2, len(px)))
    nx = np.concatenate([px[1:], px[-1:]]) - np.concatenate([px[:1], px[:-1]])
    ny = np.concatenate([py[1:], py[-1:]]) - np.concatenate([py[:1], py[:-1]])
    return np.stack([nx, ny])


def _detect(
    plan_tr: pd.DataFrame,
    track: pd.DataFrame,
    until: np.datetime64,
    since: np.datetime64 | None = None,
) -> pd.DataFrame:
    """Общий обход визитов с ``since < tb <= until``."""
    tb = plan_tr["tb"].to_numpy(dtype="datetime64[ns]")
    sel = tb <= until if since is None else (tb <= until) & (tb > since)
    idx = np.flatnonzero(sel)
    ts, x, y = _valid_track(track, until)
    px, py = to_xy(plan_tr["lon"].to_numpy(), plan_tr["lat"].to_numpy())
    dirs = _route_directions(px, py)[:, idx]
    sxs, sys_ = px[idx], py[idx]
    tb_s = tb[idx].astype(np.int64) / NS
    vids = plan_tr["visit_id"].to_numpy()[idx]
    first = plan_tr["new_trip"].to_numpy()[idx] == 1
    out_v, out_t, out_d = [], [], []
    prev = -np.inf
    for k in range(len(idx)):
        lo = max(tb_s[k] - WINDOW_BEFORE_S, prev)
        a, b = np.searchsorted(ts, [lo, tb_s[k] + WINDOW_AFTER_S], side="left")
        if first[k]:
            arr = _departure_time(ts[a:b], x[a:b], y[a:b], sxs[k], sys_[k])
        else:
            arr = _arrival_time(ts[a:b], x[a:b], y[a:b], sxs[k], sys_[k], dirs[:, k])
        if arr is None:
            continue
        prev = arr
        out_v.append(int(vids[k]))
        out_t.append(arr)
        out_d.append(arr - tb_s[k])
    actual = (np.asarray(out_t, dtype=float) * NS).astype(np.int64).astype("datetime64[ns]")
    return pd.DataFrame(
        {
            "visit_id": np.asarray(out_v, dtype=np.int64),
            "actual_at": actual,
            "delay_s": np.asarray(out_d, dtype=float),
        }
    )


def detect_arrivals(plan_tr: pd.DataFrame, track: pd.DataFrame, until: datetime) -> pd.DataFrame:
    """Виртуальные факты прибытия ТС на остановки плана.

    :param plan_tr: план одного ТС (из :func:`transit_core.plan.load_plan`).
    :param track: трек ТС (``et, lon, lat, speed, heading, valid``), может содержать будущее —
        используются только точки с ``et <= until``.
    :param until: момент «сейчас».
    :return: кадр ``visit_id, actual_at, delay_s`` по визитам с ``tb <= until``, где прибытие
        найдено.
    """
    return _detect(plan_tr, track, np.datetime64(until, "ns"))


def online_cur_dev(plan_tr: pd.DataFrame, track: pd.DataFrame, t: datetime) -> float | None:
    """Онлайн-аналог ``cur_dev_s``: задержка на последней остановке с планом ``<= t``.

    Берётся задержка последнего найденного по GPS прибытия за :data:`ONLINE_LOOKBACK_S`.
    Если на последнюю плановую остановку ТС ещё не прибыло, это задержка на предыдущей
    пройденной. Нижняя граница ``t − tb`` не используется: на конечных она равна длине
    отстоя и даёт ошибку в сотни секунд (проверено на labels_test).

    :return: задержка в секундах или None, если прибытий за окно нет.
    """
    until = np.datetime64(t, "ns")
    arr = _detect(plan_tr, track, until, until - np.timedelta64(ONLINE_LOOKBACK_S, "s"))
    if arr.empty:
        return None
    return float(arr["delay_s"].iat[-1])
