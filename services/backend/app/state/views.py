"""Контрактные представления state: ``VehicleState``, ``VehicleDetail``, риск перегонов."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from transit_core import schemas as S
from transit_core.risk import RISK_ORDER

from ..alerts.segment import target_segment
from .static import StaticData
from .store import VehicleRecord
from .timefmt import fmt

TIMELINE_WINDOW = timedelta(minutes=60)
PASSED_GRACE = timedelta(minutes=10)
SERIES_WINDOW = timedelta(minutes=60)


def vehicle_state(rec: VehicleRecord, stale: bool, warming_up: bool) -> S.VehicleState:
    """Текущее состояние ТС на карте."""
    fix, p = rec.fix, rec.prediction
    return S.VehicleState(
        vehicle_id=rec.vehicle_id,
        kind=rec.kind,
        tr_id=str(rec.tr_id) if rec.tr_id is not None else None,
        unit_id=rec.unit_id,
        route_id=rec.route_id,
        route_name=rec.route_name,
        lon=fix.lon if fix else None,
        lat=fix.lat if fix else None,
        heading=float(fix.heading) if fix else None,
        speed_kmh=float(fix.speed) if fix else None,
        last_seen=fmt(rec.last_et) if rec.last_et else None,
        stale=stale,
        warming_up=warming_up,
        current_dev_s=rec.current_dev_s,
        next_stop=rec.next_stop,
        prediction=p,
        risk_level=p.risk_level if p else "none",
        open_incident_id=rec.open_incident_id,
    )


def _timeline_item(r, rec: VehicleRecord, target: str | None, now: datetime):
    vid = str(int(r.visit_id))
    arrived = rec.arrivals.get(vid)
    passed = arrived is not None or r.tb + PASSED_GRACE < now
    return S.StopTimelineItem(
        visit_id=vid,
        stop_key=str(r.stop_key),
        name=str(r.name),
        planned_at=fmt(r.tb),
        actual_at=fmt(arrived[0]) if arrived else None,
        actual_delay_s=round(arrived[1], 1) if arrived else None,
        predicted_delay_s=None if passed else rec.window_preds.get(vid),
        is_target=vid == target,
        status="passed" if passed else "upcoming",
    )


def timeline(plan_tr: pd.DataFrame, rec: VehicleRecord, now: datetime) -> list:
    """«Нитка графика»: план, виртуальный факт и прогноз в окне ±60 мин."""
    win = plan_tr[
        (plan_tr["tb"] >= now - TIMELINE_WINDOW) & (plan_tr["tb"] <= now + TIMELINE_WINDOW)
    ]
    target = rec.prediction.target_stop.visit_id if rec.prediction else None
    return [_timeline_item(r, rec, target, now) for r in win.itertuples()]


def vehicle_detail(
    static: StaticData, rec: VehicleRecord, state: S.VehicleState, now: datetime, incidents: list
) -> S.VehicleDetail:
    """Ответ ``GET /api/v1/vehicles/{id}``."""
    plan_tr = static.plan_of(rec.tr_id)
    p = rec.prediction
    forecast = None
    if p is not None and p.target_stop.planned_at:
        forecast = S.ForecastPoint(t=p.target_stop.planned_at, delay_s=p.predicted_delay_s,
                                   lo_s=p.interval_s[0], hi_s=p.interval_s[1])  # fmt: skip
    series = [
        S.DeviationPoint(t=fmt(t), dev_s=round(d, 1))
        for t, d in rec.dev_series
        if t >= now - SERIES_WINDOW
    ]
    return S.VehicleDetail(
        vehicle=state,
        timeline=timeline(plan_tr, rec, now) if plan_tr is not None else [],
        deviation_series=series,
        forecast=forecast,
        incident_ids=[i.id for i in incidents if i.vehicle_id == rec.vehicle_id],
    )


class _SegAcc:
    """Накопитель риска по перегону."""

    def __init__(self, route_id: str) -> None:
        self.route_id = route_id
        self.level: S.RiskLevel = "none"
        self.ids: list[str] = []
        self.ratios: list[float] = []

    def add(self, vehicle_id: str, level: S.RiskLevel, ratio: float | None) -> None:
        if vehicle_id not in self.ids:
            self.ids.append(vehicle_id)
        if RISK_ORDER[level] >= RISK_ORDER[self.level]:
            self.level = level
        if ratio is not None:
            self.ratios.append(ratio)

    def contract(self, sid: str) -> S.SegmentRisk:
        ratio = round(min(self.ratios), 3) if self.ratios else None
        return S.SegmentRisk(segment_id=sid, route_id=self.route_id, risk_level=self.level,
                             vehicle_ids=self.ids, speed_ratio=ratio)  # fmt: skip


def segments_risk(
    static: StaticData, states: list[S.VehicleState], current: dict | None = None
) -> list[S.SegmentRisk]:
    """Риск по перегонам для окраски карты.

    * перегон к целевой остановке прогноза — риск прогноза;
    * текущий перегон ТС (``current``: vehicle_id → SegmentNow) — риск ТС и
      ``speed_ratio`` = средняя скорость за 5 мин / типичная (минимум по ТС на перегоне).
    """
    acc: dict[str, _SegAcc] = {}
    current = current or {}
    for st in states:
        if st.route_id is None or st.tr_id is None:
            continue
        now = current.get(st.vehicle_id)
        if now is not None:
            acc.setdefault(now.segment_id, _SegAcc(st.route_id)).add(
                st.vehicle_id, st.risk_level, now.ratio
            )
        p, plan_tr = st.prediction, static.plan_of(int(st.tr_id))
        if p is None or plan_tr is None:
            continue
        seg = target_segment(plan_tr, int(st.tr_id), p.target_stop)
        if seg is not None and seg.segment_id is not None:
            acc.setdefault(seg.segment_id, _SegAcc(st.route_id)).add(
                st.vehicle_id, p.risk_level, None
            )
    return [a.contract(sid) for sid, a in acc.items()]
