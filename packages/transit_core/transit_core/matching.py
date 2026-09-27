"""Map matching: привязка точек NDTP к нитке рейса с монотонным прогрессом.

:class:`RouteMatcher` живёт на одном ТС и получает точки по одной (онлайн). Учитывается
специфика NDTP-телеметрии:

* ``valid``-бит (``extraDopBit7``) и нулевые координаты у невалидных точек — точка
  пропускается и считается «нет данных»;
* координаты с шагом 1e-7, целые скорость и курс; при скорости ниже
  :data:`COURSE_MIN_KMH` курс недостоверен и не используется;
* GPS-выбросы: скачок, дающий скорость выше :data:`MAX_KMH` (с допуском на округление
  времени до секунд), и «телепорт» после долгого разрыва — точка отбрасывается; после
  :data:`RESYNC_N` отбросов подряд якорь переносится (выбросом был сам якорь);
* стоянка (скорость ≈ 0 и дрожание GPS в пределах :data:`STILL_M`) не двигает прогресс.

Проекция ищется на отрезках нитки в окне вокруг текущего прогресса; скоринг кандидата —
поперечное расстояние + штраф за откат назад + несогласие курса с азимутом отрезка + слабый
приор «прогресс по расписанию». Прогресс не убывает. Рейс меняется на конечной (по плану и
проекции); при потере привязки рейс ищется заново по окну расписания.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np
import pandas as pd

from transit_core.route_line import Projection, TripLine, build_lines
from transit_core.track import to_xy

MAX_KMH = 120.0
TELEPORT_KMH = 80.0
LONG_GAP_S = 60.0
JITTER_M = 50.0
STILL_KMH = 3.0
STILL_M = 30.0
RESYNC_N = 3
COURSE_MIN_KMH = 5.0
COURSE_W = 80.0
BACK_TOL_M = 30.0
BACK_W = 2.0
BACK_WIN_M = 300.0
AHEAD_M = 300.0
PRIOR_W = 0.05
ACQ_PRIOR_W = 0.2
ACQ_PRE_S = 30 * 60.0
ACQ_POST_S = 20 * 60.0
ACQUIRE_MAX_M = 300.0
ON_ROUTE_M = 120.0
LOST_M = 400.0
LOST_N = 8
END_ZONE_M = 400.0
ARRIVE_M = 60.0
NEXT_PRE_S = 30 * 60.0
TRIP_OVERDUE_S = 30 * 60.0
CONF_SCALE_M = 80.0
COURSE_CONF_W = 0.5
"""Доля уверенности, которую снимает курс против азимута отрезка (нитка — хорды улиц)."""
WARM_N = 3
_EPOCH = datetime(1970, 1, 1)


def to_seconds(t: datetime | float) -> float:
    """Секунды эпохи для naive-времени датасета (как ``datetime64 → int64`` в плане)."""
    if isinstance(t, datetime):
        return (t.replace(tzinfo=None) - _EPOCH).total_seconds()
    return float(t)


@dataclass(frozen=True)
class MatchResult:
    """Привязка точки к нитке.

    ``prev_stop``/``next_stop`` — позиции строк в плане ТС (``plan_tr`` со сброшенным
    индексом); ``next_stop`` = None в конце рейса. ``offset_m`` — поперечное отклонение.
    """

    t_s: float
    trip: int
    progress_m: float
    segment_idx: int
    offset_m: float
    prev_stop: int
    next_stop: int | None
    dist_to_next_m: float | None
    on_route: bool
    confidence: float


@dataclass(frozen=True)
class _Cand:
    """Лучший кандидат-проекция: нитка, дистанция, поперечное расстояние, косинус курса."""

    line: int
    s: float
    dist: float
    cos_c: float
    score: float


def schedule_deviation_s(match: MatchResult, line: TripLine, t: datetime | float) -> float:
    """GPS-отклонение от нитки графика, сек (> 0 — опоздание).

    Плановое время для текущего прогресса — интерполяция между плановыми временами соседних
    остановок (:meth:`TripLine.planned_at`); отклонение = ``t`` − это время.

    До планового отправления рейса ТС на отстое: отклонение 0, а не «опережение» на
    остаток отстоя. Иначе ТС, вставшее в паре метров за точкой конечной (или на второй
    точке конечной), получало −(минуты до отправления) — до −75 мин на validate — и
    спорило по знаку с задержкой прибытия на конечную у стоп-детектора.
    """
    t_s = to_seconds(t)
    if t_s < line.t_start:
        return 0.0
    return t_s - line.planned_at(match.progress_m, t_s)


def _score(line: TripLine, pr: Projection, t: float, prior_w: float, back_from: float | None):
    """Скоринг кандидатов: расстояние + откат + курс + приор расписания."""
    score = pr.dist + COURSE_W * (1.0 - pr.cos_course) / 2.0 * (pr.cos_course != 0)
    score = score + prior_w * np.abs(t - np.interp(pr.s, line.cum, line.tb))
    if back_from is not None:
        score = score + BACK_W * np.maximum(back_from - pr.s - BACK_TOL_M, 0.0)
    return score


def _best(i: int, line: TripLine, pr: Projection, score: np.ndarray, extra: float = 0.0) -> _Cand:
    j = int(np.argmin(score))
    return _Cand(
        i, float(pr.s[j]), float(pr.dist[j]), float(pr.cos_course[j]), float(score[j]) + extra
    )


class RouteMatcher:
    """Онлайн map matching одного ТС по ниткам его рейсов."""

    def __init__(self, plan_tr: pd.DataFrame | None = None, lines: list[TripLine] | None = None):
        """:param plan_tr: план ТС (индекс сброшен) или готовые ``lines`` из ``build_lines``."""
        if lines is None:
            if plan_tr is None:
                raise ValueError("нужен plan_tr или lines")
            lines = build_lines(plan_tr.reset_index(drop=True))
        self.lines = lines
        self._by_trip = {ln.trip: i for i, ln in enumerate(lines)}
        self._cur: int | None = None
        self._progress = 0.0
        self._anchor: tuple[float, float, float] | None = None  # (t, x, y)
        self._rejects = self._lost = self._since_acq = 0
        self.last: MatchResult | None = None
        self.stats: Counter[str] = Counter()

    def line_of(self, trip: int) -> TripLine | None:
        """Нитка рейса ``trip`` или None."""
        i = self._by_trip.get(trip)
        return None if i is None else self.lines[i]

    def deviation_s(self, t: datetime | float | None = None) -> float | None:
        """:func:`schedule_deviation_s` для последней привязки (по умолчанию на её момент)."""
        m = self.last
        if m is None:
            return None
        line = self.line_of(m.trip)
        return schedule_deviation_s(m, line, m.t_s if t is None else t) if line else None

    def update(
        self, t: datetime | float, lon: float, lat: float, speed: float, course: float, valid: bool
    ) -> MatchResult | None:
        """Очередная точка NDTP → привязка или None (точка пропущена/отброшена)."""
        self.stats["points"] += 1
        if not valid or lon == 0.0 or lat == 0.0:
            self.stats["invalid"] += 1
            return None
        t_s = to_seconds(t)
        px, py = (float(v) for v in to_xy(lon, lat))
        kind, dt = self._screen(t_s, px, py, speed)
        if kind == "outlier":
            self.stats["outlier"] += 1
            return None
        if kind == "still" and self.last is not None:
            self.stats["still"] += 1
            self._roll_to_next(t_s, px, py)
            self.last = replace(self.last, t_s=t_s)
            return self.last
        self._anchor = (t_s, px, py)
        crs = float(course) if speed >= COURSE_MIN_KMH else None
        res = self._match(t_s, px, py, crs, dt)
        self.stats["matched" if res is not None else "unmatched"] += 1
        self.last = res if res is not None else self.last
        return res

    def _screen(self, t_s: float, px: float, py: float, speed: float) -> tuple[str, float | None]:
        """Фильтр выбросов и стоянки относительно якоря: ``ok``/``outlier``/``still``.

        Точка из прошлого (пришла не по порядку) отбрасывается, но не считается выбросом
        для смены якоря.
        """
        if self._anchor is None:
            return "ok", None
        at, ax, ay = self._anchor
        dt, d = t_s - at, float(np.hypot(px - ax, py - ay))
        if dt < 0:
            self.stats["late"] += 1
            return "outlier", None
        vmax = (MAX_KMH if dt <= LONG_GAP_S else TELEPORT_KMH) / 3.6
        if d > JITTER_M + vmax * dt:
            self._rejects += 1
            if self._rejects < RESYNC_N:
                return "outlier", None
            self.stats["resync"] += 1
            self._cur = None  # выбросом был якорь: ищем привязку заново
        self._rejects = 0
        if speed < STILL_KMH and d < STILL_M and self._cur is not None:
            self._anchor = (t_s, ax, ay)
            return "still", dt
        return "ok", max(dt, 0.0)

    def _match(self, t: float, px: float, py: float, crs: float | None, dt: float | None):
        """Привязка точки: сопровождение текущего рейса или поиск рейса заново."""
        cur = self.lines[self._cur] if self._cur is not None else None
        if cur is None or dt is None or t > cur.t_end + TRIP_OVERDUE_S:
            return self._acquire(t, px, py, crs)
        best = self._track(t, px, py, crs, dt)
        if best.dist > LOST_M:
            self._lost += 1
            if self._lost >= LOST_N:
                return self._acquire(t, px, py, crs)
            return self._result(t, self._cur, self._progress, best.dist, 0.0, on_route=False)
        self._lost = 0
        if best.line != self._cur:
            self.stats["switch"] += 1
            self._cur, self._progress, self._since_acq = best.line, best.s, 0
        self._progress = max(self._progress, best.s)
        self._since_acq += 1
        return self._result(t, self._cur, self._progress, best.dist, best.cos_c)

    def _track(self, t: float, px: float, py: float, crs: float | None, dt: float) -> _Cand:
        """Лучший кандидат на текущей нитке (окно вокруг прогресса) и начале следующей."""
        i, p = self._cur, self._progress
        line = self.lines[i]
        reach = MAX_KMH / 3.6 * dt + AHEAD_M
        a, b = line.window(p - BACK_WIN_M, p + reach)
        pr = line.project(px, py, a, b, crs)
        best = _best(i, line, pr, _score(line, pr, t, PRIOR_W, p))
        nxt = self.lines[i + 1] if i + 1 < len(self.lines) else None
        near_end = line.length - p < END_ZONE_M or t > line.t_end
        if nxt is not None and near_end and t >= nxt.t_start - NEXT_PRE_S:
            a, b = nxt.window(0.0, reach)
            pr = nxt.project(px, py, a, b, crs)
            cand = _best(i + 1, nxt, pr, _score(nxt, pr, t, PRIOR_W, None))
            best = cand if cand.score < best.score else best
        return best

    def _roll_to_next(self, t: float, px: float, py: float) -> None:
        """Стоянка на конечной после планового прибытия → привязка к началу следующего рейса."""
        i = self._cur
        if i is None or i + 1 >= len(self.lines):
            return
        line, nxt = self.lines[i], self.lines[i + 1]
        if line.length - self._progress > ARRIVE_M or t < line.t_end:
            return
        a, b = nxt.window(0.0, ARRIVE_M)
        pr = nxt.project(px, py, a, b, None)
        j = int(np.argmin(pr.dist))
        if pr.dist[j] <= ON_ROUTE_M:
            self.stats["switch"] += 1
            self._cur, self._progress, self._since_acq = i + 1, float(pr.s[j]), 0
            self.last = self._result(t, i + 1, self._progress, float(pr.dist[j]), 0.0)

    def _acquire(self, t: float, px: float, py: float, crs: float | None) -> MatchResult | None:
        """Поиск рейса по окну расписания и проекции на всю нитку."""
        self.stats["acquire"] += 1
        best: _Cand | None = None
        for i, line in enumerate(self.lines):
            if not line.t_start - ACQ_PRE_S <= t <= line.t_end + ACQ_POST_S:
                continue
            pr = line.project(px, py, 0, len(line.seg_len), crs)
            cand = _best(i, line, pr, _score(line, pr, t, ACQ_PRIOR_W, None))
            best = cand if best is None or cand.score < best.score else best
        if best is None or best.dist > ACQUIRE_MAX_M:
            self._cur = None
            return None
        self._cur, self._progress, self._lost, self._since_acq = best.line, best.s, 0, 0
        return self._result(t, best.line, best.s, best.dist, best.cos_c)

    def _result(
        self, t: float, i: int, s: float, dist: float, cos_c: float, on_route: bool = True
    ) -> MatchResult:
        """Контракт привязки для рейса ``i`` и прогресса ``s``."""
        line = self.lines[i]
        prev, nxt = line.stops_around(s)
        on = on_route and dist <= ON_ROUTE_M
        conf = np.exp(-dist / CONF_SCALE_M) * min(1.0, (self._since_acq + 1) / WARM_N)
        conf *= 1.0 - COURSE_CONF_W * (1.0 - cos_c) / 2.0 if cos_c != 0 else 1.0
        return MatchResult(
            t_s=t,
            trip=line.trip,
            progress_m=float(s),
            segment_idx=min(prev, len(line.seg_len) - 1),
            offset_m=round(float(dist), 2),
            prev_stop=int(line.rows[prev]),
            next_stop=None if nxt is None else int(line.rows[nxt]),
            dist_to_next_m=None if nxt is None else round(float(line.cum[nxt] - s), 2),
            on_route=bool(on),
            confidence=round(float(conf) if on else 0.0, 3),
        )


MATCH_COLUMNS = [
    "t_s", "trip", "progress_m", "segment_idx", "offset_m", "on_route", "confidence", "acquired",
]  # fmt: skip


def match_track(
    matcher: RouteMatcher, track: pd.DataFrame, timings: list[float] | None = None
) -> pd.DataFrame:
    """Прогон трека (``et, lon, lat, speed, heading, valid``) через матчер точка за точкой.

    :return: кадр :data:`MATCH_COLUMNS` по точкам, получившим привязку (as-of: каждая
        строка зависит только от точек до неё); ``acquired`` — привязка найдена заново
        (первая точка, смена якоря после выбросов, потеря привязки), только тогда прогресс
        может уменьшиться. В ``timings`` (если передан) — секунды на каждую точку.
    """
    cols = ["lon", "lat", "speed", "heading", "valid"]
    et_s = track["et"].to_numpy(dtype="datetime64[ns]").astype(np.int64) / 1e9
    rows = []
    for t_s, (lon, lat, spd, crs, ok) in zip(
        et_s, track[cols].itertuples(index=False), strict=True
    ):
        n_acq, t0 = matcher.stats["acquire"], time.perf_counter()
        m = matcher.update(float(t_s), lon, lat, spd, crs, bool(ok))
        if timings is not None:
            timings.append(time.perf_counter() - t0)
        if m is not None:
            rows.append((m.t_s, m.trip, m.progress_m, m.segment_idx, m.offset_m,
                         m.on_route, m.confidence, matcher.stats["acquire"] > n_acq))  # fmt: skip
    return pd.DataFrame(rows, columns=MATCH_COLUMNS)
