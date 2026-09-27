"""Подготовка прохода прогнозов (выполняется в thread-worker'е).

Для каждого ТС с расписанием: отклонение сейчас (``online_cur_dev``), виртуальные
факты прибытия, визиты в окне ``(T+10, T+15]`` и as-of признаки для каждого визита.
Функции чистые: получают копии треков и ничего не меняют в state.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from transit_core.segment_speed import current_segment, dwell_seconds, speed_ratio

from ..state.static import StaticData
from ..state.store import track_from_points
from .adapters import detect_arrivals, online_cur_dev, point_features

log = logging.getLogger(__name__)
HORIZON = (timedelta(minutes=10), timedelta(minutes=15))
LOOKAHEAD = timedelta(hours=1)
DETECTOR_MARGIN = timedelta(minutes=30)


@dataclass(frozen=True)
class VehicleInput:
    """Снимок ТС для прохода: копия точек буфера (DataFrame строится в потоке) и флаги."""

    vehicle_id: str
    tr_id: int
    points: list
    stale: bool
    ready: bool  # истории хватает для прогноза (≥ 15 сим-мин)


@dataclass(frozen=True)
class VisitTask:
    """Визит-кандидат для прогноза."""

    item_id: str
    visit_id: str
    trip: int
    row: object  # namedtuple строки плана
    lead_min: float
    features: dict


@dataclass(frozen=True)
class SegmentNow:
    """Текущий перегон ТС: средняя скорость за 5 мин и отношение к типичной."""

    segment_id: str
    speed_kmh: float
    typical_kmh: float | None
    ratio: float | None


@dataclass
class VehicleTask:
    """Результат подготовки по одному ТС."""

    vehicle_id: str
    tr_id: int
    stale: bool
    ready: bool
    cur_dev: float | None = None
    arrivals: dict[str, tuple[datetime, float]] = field(default_factory=dict)
    visits: list[VisitTask] = field(default_factory=list)
    horizon_ok: bool = False
    segment: SegmentNow | None = None
    dwell_s: float | None = None

    def extras(self) -> dict[str, float]:
        """Производные признаки бэкенда для причины и evidence (в ML не уходят)."""
        out: dict[str, float] = {}
        if self.dwell_s is not None:
            out["dwell"] = self.dwell_s
            if self.dwell_s > 0 and self.in_layover():
                out["in_layover"] = 1.0
        if self.segment is not None:
            out["seg_speed"] = self.segment.speed_kmh
            if self.segment.ratio is not None:
                out["speed_ratio"] = self.segment.ratio
        return out

    next_row: object | None = None

    def in_layover(self) -> bool:
        """Стоит до планового начала следующего рейса — это отстой, а не задержка."""
        return bool(getattr(self.next_row, "new_trip", 0))


def horizon_visits(plan_tr: pd.DataFrame, t: datetime) -> tuple[pd.DataFrame, bool]:
    """Визиты в окне ``(t+10, t+15]``; если окно пустое — ближайший визит после (вне окна)."""
    lo, hi = t + HORIZON[0], t + HORIZON[1]
    window = plan_tr[(plan_tr["tb"] > lo) & (plan_tr["tb"] <= hi)]
    if len(window):
        return window, True
    later = plan_tr[(plan_tr["tb"] > hi) & (plan_tr["tb"] <= t + LOOKAHEAD)]
    return later.head(1), False


def detector_plan(plan_tr: pd.DataFrame, track: pd.DataFrame) -> pd.DataFrame:
    """План только за время, покрытое треком (±запас): детектор не перебирает весь день."""
    lo = track["et"].iloc[0] - DETECTOR_MARGIN
    return plan_tr[plan_tr["tb"] >= lo].reset_index(drop=True)


def _arrivals(plan_tr: pd.DataFrame, track: pd.DataFrame, t: datetime) -> dict:
    arr = detect_arrivals(plan_tr, track, t)
    return {
        str(int(r.visit_id)): (
            pd.Timestamp(r.actual_at).floor("s").to_pydatetime(),
            float(r.delay_s),
        )
        for r in arr.itertuples()
        if pd.notna(r.actual_at)
    }


def _visit_tasks(
    vi: VehicleInput, plan_tr: pd.DataFrame, track: pd.DataFrame, t: datetime, cur_dev
) -> tuple:
    rows, ok = horizon_visits(plan_tr, t)
    tasks = []
    for r in rows.itertuples():
        feats = point_features(plan_tr, track, t, int(r.visit_id), cur_dev)
        tasks.append(
            VisitTask(
                item_id=f"{vi.tr_id}:{int(r.visit_id)}",
                visit_id=str(int(r.visit_id)),
                trip=int(r.trip),
                row=r,
                lead_min=round((r.tb - t).total_seconds() / 60, 2),
                features=dict(feats),
            )
        )
    return tasks, ok


def segment_now(
    static: StaticData, tr_id: int, track: pd.DataFrame, t: datetime, typical: Mapping
) -> SegmentNow | None:
    """Где ТС сейчас и насколько медленнее обычного едет по этому перегону."""
    segs = static.segments.get(tr_id)
    cur = current_segment(segs, track, t) if segs is not None else None
    if cur is None:
        return None
    typ = typical.get(cur.segment_id)
    return SegmentNow(cur.segment_id, cur.speed_kmh, typ, speed_ratio(cur.speed_kmh, typ))


def prepare_vehicle(
    static: StaticData, vi: VehicleInput, t: datetime, typical: Mapping | None = None
) -> VehicleTask:
    """Подготовка одного ТС к проходу на сим-минуту ``t``."""
    plan_tr = static.plans[vi.tr_id]
    task = VehicleTask(vi.vehicle_id, vi.tr_id, vi.stale, vi.ready)
    upcoming = plan_tr[plan_tr["tb"] > t]
    task.next_row = next(upcoming.head(1).itertuples(), None)
    if not vi.points:
        return task
    track = track_from_points(vi.points)
    det_plan = detector_plan(plan_tr, track)
    cur = online_cur_dev(det_plan, track, t)
    task.cur_dev = None if cur is None or pd.isna(cur) else float(cur)
    task.arrivals = _arrivals(det_plan, track, t)
    task.segment = segment_now(static, vi.tr_id, track, t, typical or {})
    task.dwell_s = dwell_seconds(track, t)
    if vi.ready:
        task.visits, task.horizon_ok = _visit_tasks(vi, plan_tr, track, t, task.cur_dev)
    return task


def prepare(
    static: StaticData, inputs: list[VehicleInput], t: datetime, typical: Mapping | None = None
) -> list[VehicleTask]:
    """Подготовка всех ТС; ошибка в одном ТС не ломает проход для остальных."""
    out = []
    for vi in inputs:
        try:
            out.append(prepare_vehicle(static, vi, t, typical))
        except Exception:  # noqa: BLE001
            log.exception("Не удалось подготовить прогноз для ТС %s", vi.vehicle_id)
    return out
