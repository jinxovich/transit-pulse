"""Нитка рейса: полилиния по плановым остановкам и проекция точки на неё.

Нитка строится по остановкам одного рейса плана (``trip`` из :func:`transit_core.plan.load_plan`)
в локальных метрах (:func:`transit_core.track.to_xy`). Для каждой остановки известны
накопленная дистанция по нитке и плановое время — отсюда плановое время любой точки нитки
(линейная интерполяция между соседними остановками).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from transit_core.track import to_xy

NS = 1_000_000_000
AT_STOP_M = 0.5
"""Допуск «ТС ровно на остановке» для плановых окон (погрешность float)."""


@dataclass(frozen=True)
class Projection:
    """Проекции точки на отрезки ``[a, b)`` нитки (массивы длины ``b − a``)."""

    seg: np.ndarray  # индексы отрезков
    s: np.ndarray  # дистанция проекции по нитке, м
    dist: np.ndarray  # поперечное расстояние до отрезка, м
    cos_course: np.ndarray  # косинус угла между курсом и азимутом отрезка (0 — неизвестно)


@dataclass(frozen=True)
class TripLine:
    """Полилиния одного рейса ТС.

    ``rows`` — позиции остановок рейса в плане ТС (``plan_tr`` со сброшенным индексом).
    Отрезок ``k`` соединяет остановки ``k`` и ``k + 1``.
    """

    trip: int
    rows: np.ndarray
    x: np.ndarray
    y: np.ndarray
    cum: np.ndarray
    tb: np.ndarray  # плановое время остановок, секунды эпохи (naive = UTC)
    seg_len: np.ndarray
    ux: np.ndarray  # единичный вектор отрезка (восток)
    uy: np.ndarray  # единичный вектор отрезка (север)

    @property
    def length(self) -> float:
        """Длина нитки, м."""
        return float(self.cum[-1])

    @property
    def t_start(self) -> float:
        """Плановое время первой остановки рейса."""
        return float(self.tb[0])

    @property
    def t_end(self) -> float:
        """Плановое время последней остановки рейса."""
        return float(self.tb[-1])

    def window(self, s_lo: float, s_hi: float) -> tuple[int, int]:
        """Отрезки ``[a, b)``, пересекающие интервал дистанций ``[s_lo, s_hi]``."""
        a = int(np.searchsorted(self.cum, s_lo, side="right")) - 1
        b = int(np.searchsorted(self.cum, s_hi, side="left"))
        return max(a, 0), min(max(b, a + 1), len(self.seg_len))

    def project(self, px: float, py: float, a: int, b: int, course: float | None) -> Projection:
        """Проекция точки на отрезки ``[a, b)`` (векторно).

        :param course: курс NDTP в градусах (0 — север, по часовой) или None, если ТС стоит.
        """
        ax, ay = self.x[a:b], self.y[a:b]
        ux, uy, ln = self.ux[a:b], self.uy[a:b], self.seg_len[a:b]
        u = np.clip((px - ax) * ux + (py - ay) * uy, 0.0, ln)
        dist = np.hypot(ax + u * ux - px, ay + u * uy - py)
        if course is None:
            cos_c = np.zeros(b - a)
        else:
            rad = np.deg2rad(course)
            cos_c = np.sin(rad) * ux + np.cos(rad) * uy
        return Projection(np.arange(a, b), self.cum[a:b] + u, dist, cos_c)

    def stops_around(self, s: float) -> tuple[int, int | None]:
        """``(предыдущая, следующая)`` остановки для дистанции ``s`` (индексы внутри рейса)."""
        prev = int(np.searchsorted(self.cum, s, side="right")) - 1
        prev = min(max(prev, 0), len(self.cum) - 1)
        nxt = prev + 1
        return prev, (nxt if nxt < len(self.cum) else None)

    def planned_at(self, s: float, t: float) -> float:
        """Плановое время прохождения точки ``s`` нитки с учётом стоянок.

        Между остановками — линейная интерполяция плановых времён. Если ``s`` ровно на
        остановке (или на нескольких совпадающих), план — окно ``[tb_первой, tb_последней]``:
        ожидание внутри окна не считается отклонением. До отправления с первой остановки
        рейса (отстой на конечной) ТС не опаздывает, пока плановое время не наступило.
        """
        lo_i = int(np.searchsorted(self.cum, s - AT_STOP_M, side="left"))
        hi_i = int(np.searchsorted(self.cum, s + AT_STOP_M, side="right"))
        if hi_i > lo_i:
            lo = -np.inf if lo_i == 0 else float(self.tb[lo_i])
            return float(np.clip(t, lo, float(self.tb[hi_i - 1])))
        k = min(max(lo_i - 1, 0), len(self.seg_len) - 1)
        frac = (s - self.cum[k]) / self.seg_len[k] if self.seg_len[k] > 0 else 1.0
        frac = min(max(frac, 0.0), 1.0)
        return float(self.tb[k] + frac * (self.tb[k + 1] - self.tb[k]))


def _line(trip: int, rows: np.ndarray, plan_tr: pd.DataFrame) -> TripLine:
    """Нитка по строкам ``rows`` плана."""
    x, y = to_xy(plan_tr["lon"].to_numpy()[rows], plan_tr["lat"].to_numpy()[rows])
    dx, dy = np.diff(x), np.diff(y)
    seg_len = np.hypot(dx, dy)
    safe = np.where(seg_len > 0, seg_len, 1.0)
    tb = plan_tr["tb"].to_numpy(dtype="datetime64[ns]")[rows].astype(np.int64) / NS
    return TripLine(
        trip=trip,
        rows=rows,
        x=x,
        y=y,
        cum=np.concatenate([[0.0], np.cumsum(seg_len)]),
        tb=tb,
        seg_len=seg_len,
        ux=np.where(seg_len > 0, dx / safe, 0.0),
        uy=np.where(seg_len > 0, dy / safe, 0.0),
    )


def build_lines(plan_tr: pd.DataFrame) -> list[TripLine]:
    """Нитки всех рейсов ТС в плановом порядке (рейсы из одной остановки пропускаются).

    :param plan_tr: план одного ТС, отсортирован по ``tb``, индекс сброшен.
    """
    trips = plan_tr["trip"].to_numpy()
    out = []
    for trip in pd.unique(trips):
        rows = np.flatnonzero(trips == trip)
        if len(rows) >= 2:
            out.append(_line(int(trip), rows, plan_tr))
    return out
