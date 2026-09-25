"""Жизненный цикл инцидентов в моках: open → (ack) → resolved."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from contracts.mockgen.common import ACK_FRAME, DEGRADED, fmt, frame_time, ws
from contracts.mockgen.fleet import VehicleCtx, stop_ref
from transit_core import schemas as S
from transit_core.catalog import evidence, format_delay, make_cause, recommendations_for

UPDATE_STEP_S = 30


def evidence_for(cause: S.CauseCode, d: float) -> list[S.Evidence]:
    """Правдоподобные обоснования для синтетических инцидентов."""
    if cause == "CONGESTION":
        return [
            evidence("spd5", "6 км/ч", round(0.45 * d, 1)),
            evidence("speed_ratio", "0.35", round(0.3 * d, 1)),
            evidence("cur_dev", format_delay(0.6 * d), round(0.2 * d, 1)),
        ]
    if cause == "ACCUMULATED_DELAY":
        return [
            evidence("cur_dev", format_delay(0.85 * d), round(0.6 * d, 1)),
            evidence("dev_trend", "+15 с / 5 мин", round(0.2 * d, 1)),
            evidence("layover", "нет", round(0.1 * d, 1)),
        ]
    return [evidence("cur_dev", format_delay(0.85 * d), round(0.5 * d, 1))]


def incident_segment(ctx: VehicleCtx, target: S.StopRef) -> S.Segment | None:
    """Перегон «предыдущая остановка → целевая» по плану ТС."""
    plan = ctx.plan.reset_index(drop=True)
    idx = plan.index[plan["tt_action_item_id"].astype(str) == target.visit_id]
    if not len(idx) or idx[0] == 0:
        return None
    prev, cur = plan.iloc[idx[0] - 1], plan.iloc[idx[0]]
    same_trip = prev["trip"] == cur["trip"]
    return S.Segment(
        segment_id=f"{ctx.route_id}:{prev['stop_key']}>{cur['stop_key']}" if same_trip else None,
        from_stop=stop_ref(prev),
        to_stop=target,
        geometry=S.LineString(coordinates=[(prev["lon"], prev["lat"]), (cur["lon"], cur["lat"])]),
    )


class IncidentBook:
    """Хранит инциденты сессии и порождает WS-события по их жизненному циклу."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.all: dict[str, S.Incident] = {}
        self.open_by_vehicle: dict[str, str] = {}
        self.acked = False
        # История статусов/исходов по всем событиям — для проверки покрытия enum'ов.
        self.seen: dict[str, set[str]] = {"status": set(), "outcome": set()}

    def step(self, ctxs: dict[str, VehicleCtx], states: dict, frame: int) -> list[dict]:
        """Продвигает инциденты на один кадр и возвращает WS-сообщения."""
        t = frame_time(frame)
        msgs = []
        for vid, st in states.items():
            if vid in self.open_by_vehicle:
                msgs += self._advance(self.all[self.open_by_vehicle[vid]], st, t, frame)
            elif self._should_open(st, frame):
                msgs.append(self._open(ctxs[vid], st, t))
        return msgs

    def _emit(self, kind: str, inc: S.Incident, t: datetime) -> dict:
        self.all[inc.id] = inc
        self.seen["status"].add(inc.status)
        self.seen["outcome"].add(inc.outcome)
        return ws(kind, t, inc)

    @staticmethod
    def _should_open(st: S.VehicleState, frame: int) -> bool:
        p = st.prediction
        red = p is not None and p.risk_level == "red" and p.horizon_ok
        return red and not st.warming_up and frame not in DEGRADED

    def _open(self, ctx: VehicleCtx, st: S.VehicleState, t: datetime) -> dict:
        p = st.prediction
        inc = S.Incident(
            id=f"inc-{len(self.all) + 1:04d}", vehicle_id=st.vehicle_id, tr_id=st.tr_id,
            route_name=st.route_name, status="open", risk_level="red", created_at=fmt(t),
            updated_at=fmt(t), lead_min=p.lead_min, target_stop=p.target_stop,
            predicted_delay_s=p.predicted_delay_s, p_late=p.p_late, interval_s=p.interval_s,
            expected_abs_error_s=p.expected_abs_error_s,
            cause=make_cause(p.cause_code, evidence_for(p.cause_code, p.predicted_delay_s)),
            segment=incident_segment(ctx, p.target_stop),
            recommendations=recommendations_for(p.cause_code), ack=None, actual_delay_s=None,
            outcome="pending",
        )  # fmt: skip
        self.open_by_vehicle[st.vehicle_id] = inc.id
        return self._emit("incident.opened", inc, t)

    def _resolve(self, inc: S.Incident, t: datetime) -> dict:
        actual = round(inc.predicted_delay_s + self.rng.uniform(-60, 45), 1)
        outcome = "hit" if actual > S.Thresholds().red_delay_s else "false_alarm"
        upd = {"status": "resolved", "updated_at": fmt(t), "actual_delay_s": actual}
        del self.open_by_vehicle[inc.vehicle_id]
        return self._emit(
            "incident.resolved", inc.model_copy(update={**upd, "outcome": outcome}), t
        )

    def _advance(self, inc: S.Incident, st: S.VehicleState, t: datetime, frame: int) -> list[dict]:
        planned = datetime.fromisoformat(inc.target_stop.planned_at)
        if t >= planned + timedelta(seconds=max(inc.predicted_delay_s, 0)):
            return [self._resolve(inc, t)]
        if frame >= ACK_FRAME and not self.acked and inc.status == "open":
            self.acked = True
            ack = S.AckInfo(
                action_code="DRIVER_CONTACT", comment="Связались с водителем", at=fmt(t)
            )
            upd = {"status": "ack", "ack": ack, "updated_at": fmt(t)}
            return [self._emit("incident.updated", inc.model_copy(update=upd), t)]
        p = st.prediction
        same_target = p is not None and p.target_stop.visit_id == inc.target_stop.visit_id
        if same_target and abs(p.predicted_delay_s - inc.predicted_delay_s) >= UPDATE_STEP_S:
            upd = {
                "predicted_delay_s": p.predicted_delay_s,
                "p_late": p.p_late,
                "interval_s": p.interval_s,
                "updated_at": fmt(t),
            }
            return [self._emit("incident.updated", inc.model_copy(update=upd), t)]
        return []
