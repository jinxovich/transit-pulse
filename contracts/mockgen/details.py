"""Детали ТС («нитка графика», ряд отклонения) и риск по перегонам."""

from __future__ import annotations

from datetime import timedelta

from contracts.mockgen.common import fmt, frame_time
from contracts.mockgen.fleet import VehicleCtx
from contracts.mockgen.incidents import IncidentBook, incident_segment
from transit_core import schemas as S

TIMELINE_WINDOW = timedelta(minutes=60)
RISK_ORDER = {"none": 0, "green": 1, "early": 2, "yellow": 3, "red": 4}


def _timeline(ctx: VehicleCtx, st: S.VehicleState, frame: int) -> list[S.StopTimelineItem]:
    t = frame_time(frame)
    plan = ctx.plan
    plan = plan[(plan["tb"] >= t - TIMELINE_WINDOW) & (plan["tb"] <= t + TIMELINE_WINDOW)]
    target = st.prediction.target_stop.visit_id if st.prediction else None
    items = []
    for r in plan.itertuples():
        d = round(ctx.scenario.delay(r.tb), 1)
        arrived = r.tb + timedelta(seconds=d)
        passed = arrived <= t
        items.append(
            S.StopTimelineItem(
                visit_id=str(r.tt_action_item_id),
                stop_key=r.stop_key,
                name=r.building_address,
                planned_at=fmt(r.tb),
                actual_at=fmt(arrived) if passed else None,
                actual_delay_s=d if passed else None,
                predicted_delay_s=None if passed else round(ctx.scenario.delay(t), 1),
                is_target=str(r.tt_action_item_id) == target,
                status="passed" if passed else "upcoming",
            )
        )
    return items


def vehicle_detail(
    ctx: VehicleCtx, st: S.VehicleState, frame: int, book: IncidentBook
) -> S.VehicleDetail:
    """Ответ ``GET /vehicles/{id}`` для ТС с расписанием."""
    t = frame_time(frame)
    series = [
        S.DeviationPoint(
            t=fmt(t - timedelta(minutes=m)),
            dev_s=round(0.85 * ctx.scenario.delay(t - timedelta(minutes=m)), 1),
        )
        for m in range(60, -1, -1)
    ]
    p = st.prediction
    forecast = None
    if p is not None:
        forecast = S.ForecastPoint(
            t=p.target_stop.planned_at,
            delay_s=p.predicted_delay_s,
            lo_s=p.interval_s[0],
            hi_s=p.interval_s[1],
        )
    return S.VehicleDetail(
        vehicle=st,
        timeline=_timeline(ctx, st, frame),
        deviation_series=series,
        forecast=forecast,
        incident_ids=[i.id for i in book.all.values() if i.vehicle_id == st.vehicle_id],
    )


def segments_risk(
    ctxs: dict[str, VehicleCtx], states: dict[str, S.VehicleState]
) -> list[S.SegmentRisk]:
    """Риск на перегонах к целевым остановкам: максимум по ТС на перегоне."""
    risks: dict[str, S.SegmentRisk] = {}
    for vid, st in states.items():
        p = st.prediction
        if p is None:
            continue
        seg = incident_segment(ctxs[vid], p.target_stop)
        if seg is None or seg.segment_id is None:
            continue
        prev = risks.get(seg.segment_id)
        if prev and RISK_ORDER[prev.risk_level] >= RISK_ORDER[p.risk_level]:
            continue
        risks[seg.segment_id] = S.SegmentRisk(
            segment_id=seg.segment_id,
            route_id=st.route_id,
            risk_level=p.risk_level,
            vehicle_ids=[vid],
            speed_ratio=0.35 if ctxs[vid].scenario.name == "congestion" else 0.95,
        )
    return list(risks.values())
