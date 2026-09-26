"""Жизненный цикл инцидентов: open → ack → resolved.

* Открываем на первом красном визите окна ``(T+10, T+15]`` — только если ТС не на
  прогреве, данные свежие и цель ещё впереди (``planned_at > created_at``).
* Дедупликация: у ТС не больше одного активного инцидента, одна цель — один инцидент.
* Resolve — по виртуальному факту прибытия на целевую остановку: ``hit``, если
  фактическое опоздание > порога red, иначе ``false_alarm``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from transit_core import schemas as S
from transit_core.catalog import recommendations_for

from ..pipeline.assemble import VisitPrediction
from ..state.timefmt import fmt, parse

UPDATE_STEP_S = 30.0
RESOLVE_TIMEOUT = timedelta(minutes=20)

Event = tuple[str, S.Incident]


@dataclass(frozen=True)
class IncidentMeta:
    """Служебные поля инцидента, которых нет в контракте."""

    vehicle_id: str
    visit_id: str
    trip: int
    planned_at: datetime


class IncidentBook:
    """Инциденты текущей сессии."""

    def __init__(self, thresholds: S.Thresholds | None = None) -> None:
        self.th = thresholds or S.Thresholds()
        self.incidents: dict[str, S.Incident] = {}
        self.meta: dict[str, IncidentMeta] = {}
        self.active: dict[str, str] = {}  # vehicle_id → incident id
        self.targets: set[tuple[str, str]] = set()
        self.trips: set[tuple[str, int]] = set()
        self._n = 0

    def clear(self) -> None:
        """Новая сессия — инциденты старой не переносятся."""
        self.__init__(self.th)

    def open_count(self) -> int:
        return len(self.active)

    def _store(self, kind: str, inc: S.Incident) -> Event:
        self.incidents[inc.id] = inc
        return kind, inc

    def open(
        self, vehicle: dict, vp: VisitPrediction, segment: S.Segment | None, t: datetime
    ) -> Event:
        """Создаёт инцидент по красному прогнозу ``vp`` (``vehicle`` — id/tr_id/маршрут)."""
        self._n += 1
        p = vp.prediction
        inc = S.Incident(
            id=f"inc-{self._n:04d}", vehicle_id=vehicle["vehicle_id"], tr_id=vehicle["tr_id"],
            route_name=vehicle["route_name"], status="open", risk_level=p.risk_level,
            created_at=fmt(t), updated_at=fmt(t), lead_min=p.lead_min, target_stop=p.target_stop,
            predicted_delay_s=p.predicted_delay_s, p_late=p.p_late, interval_s=p.interval_s,
            expected_abs_error_s=p.expected_abs_error_s, cause=vp.cause, segment=segment,
            recommendations=recommendations_for(vp.cause.code), ack=None, actual_delay_s=None,
            outcome="pending",
        )  # fmt: skip
        vid = vehicle["vehicle_id"]
        self.meta[inc.id] = IncidentMeta(vid, vp.visit.visit_id, vp.visit.trip,
                                         parse(p.target_stop.planned_at))  # fmt: skip
        self.active[vid] = inc.id
        self.targets.add((vid, vp.visit.visit_id))
        self.trips.add((vid, vp.visit.trip))
        return self._store("incident.opened", inc)

    def can_open(self, vehicle_id: str, vp: VisitPrediction, sim_now: datetime) -> bool:
        """Красный прогноз в горизонте, цель впереди и ещё не алертилась."""
        p = vp.prediction
        return (
            p.horizon_ok
            and p.risk_level == "red"
            and vehicle_id not in self.active
            and (vehicle_id, vp.visit.visit_id) not in self.targets
            and parse(p.target_stop.planned_at) > sim_now
        )

    def update(self, inc_id: str, p: S.Prediction, t: datetime) -> Event | None:
        """Новый прогноз по той же цели: событие, если он заметно изменился."""
        inc = self.incidents[inc_id]
        moved = abs(p.predicted_delay_s - inc.predicted_delay_s) >= UPDATE_STEP_S
        if not moved and p.risk_level == inc.risk_level:
            return None
        upd = {
            "predicted_delay_s": p.predicted_delay_s, "p_late": p.p_late,
            "interval_s": p.interval_s, "expected_abs_error_s": p.expected_abs_error_s,
            "risk_level": p.risk_level, "updated_at": fmt(t),
        }  # fmt: skip
        return self._store("incident.updated", inc.model_copy(update=upd))

    def resolve(self, inc_id: str, actual_s: float | None, t: datetime) -> Event:
        """Закрывает инцидент с фактической задержкой и исходом."""
        inc = self.incidents[inc_id]
        late = actual_s is not None and actual_s > self.th.red_delay_s
        upd = {
            "status": "resolved", "updated_at": fmt(t),
            "actual_delay_s": None if actual_s is None else round(actual_s, 1),
            "outcome": "hit" if late else "false_alarm",
        }  # fmt: skip
        self.active.pop(self.meta[inc_id].vehicle_id, None)
        return self._store("incident.resolved", inc.model_copy(update=upd))

    def ack(self, inc_id: str, req: S.AckRequest, t: datetime) -> Event | None:
        """Реакция диспетчера; ``None`` — нет такого инцидента."""
        inc = self.incidents.get(inc_id)
        if inc is None:
            return None
        ack = S.AckInfo(action_code=req.action_code, comment=req.comment, at=fmt(t))
        status = "resolved" if inc.status == "resolved" else "ack"
        upd = {"status": status, "ack": ack, "updated_at": fmt(t)}
        return self._store("incident.updated", inc.model_copy(update=upd))

    def settle(
        self,
        vehicle_id: str,
        arrivals: dict[str, tuple[datetime, float]],
        cur_dev: float | None,
        t: datetime,
    ) -> Event | None:
        """Закрывает активный инцидент ТС по факту прибытия на цель (или по таймауту)."""
        inc_id = self.active.get(vehicle_id)
        if inc_id is None:
            return None
        meta = self.meta[inc_id]
        if meta.visit_id in arrivals:
            return self.resolve(inc_id, arrivals[meta.visit_id][1], t)
        pred = max(self.incidents[inc_id].predicted_delay_s, 0.0)
        if t > meta.planned_at + timedelta(seconds=pred) + RESOLVE_TIMEOUT:
            return self.resolve(inc_id, cur_dev, t)
        return None
