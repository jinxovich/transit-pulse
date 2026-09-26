"""Планировщик прогнозов: раз в сим-минуту — проход по всем ТС с расписанием.

Тяжёлая часть (признаки, стоп-детектор) идёт в thread-worker'е
(``asyncio.to_thread``) над копиями треков; ML вызывается одним батчем; результаты
применяются к state в event loop. Если проход не успел до следующей сим-минуты —
промежуточные минуты пропускаются (на скорости ×600 это нормально).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from ..alerts.engine import Event
from ..alerts.segment import target_segment
from ..state.static import stop_ref
from ..state.timefmt import floor_minute
from .assemble import VisitPrediction, assemble, ml_items
from .prepare import VehicleInput, VehicleTask, prepare

if TYPE_CHECKING:
    from ..runtime import Runtime
    from ..state.store import VehicleRecord

log = logging.getLogger(__name__)
TICK_S = 0.2
ALERT_MODES = ("LIVE",)


class PipelineRunner:
    """Запускает проходы прогнозов по сим-часам."""

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.last_t: datetime | None = None
        self._epoch = -1

    def due(self) -> datetime | None:
        """Сим-минута, для которой пора делать проход, или ``None``."""
        rt = self.rt
        if rt.store is None or not rt.clock.has_session or rt.clock.state == "stopped":
            return None
        if self._epoch != rt.session_epoch:
            self.last_t, self._epoch = None, rt.session_epoch
        t = floor_minute(rt.sim_now())
        return t if self.last_t is None or t > self.last_t else None

    async def loop(self) -> None:
        """Фоновый цикл планировщика."""
        while True:
            await asyncio.sleep(TICK_S)
            t = self.due()
            if t is None:
                continue
            try:
                await self.run_pass(t)
            except Exception:  # noqa: BLE001
                log.exception("Проход прогнозов на %s упал", t)
                self.last_t = t

    def inputs(self) -> list[VehicleInput]:
        """Снимок ТС с расписанием для thread-worker'а."""
        rt = self.rt
        now, wall = rt.sim_now(), rt.wall()
        return [
            VehicleInput(rec.vehicle_id, rec.tr_id, rec.track_frame(),
                         rt.is_stale(rec, now, wall), not rt.is_warming(rec))
            for rec in rt.store.vehicles.values()
            if rec.kind == "scheduled" and rec.points
        ]  # fmt: skip

    async def run_pass(self, t: datetime) -> None:
        """Один проход: подготовка → батч в ml → прогнозы, инциденты, WS."""
        rt, epoch, t0 = self.rt, self.rt.session_epoch, time.perf_counter()
        tasks = await asyncio.to_thread(prepare, rt.static, self.inputs(), t)
        items = ml_items(tasks)
        rt.metrics.batch_size.observe(len(items))
        ml = await rt.ml.predict(items) if items else None
        if ml is not None and rt.ml.last_latency_ms is not None:
            rt.metrics.observe("ml", rt.ml.last_latency_ms)
        if epoch != rt.session_epoch:
            return  # пока считали, началась новая сессия — результат устарел
        self.last_t = t
        self.apply(tasks, ml, t)
        rt.emit("pass", t0)

    def apply(self, tasks: list[VehicleTask], ml, t: datetime) -> None:
        """Применяет результаты прохода к state и инцидентам."""
        rt = self.rt
        can_alert = rt.mode()[0] in ALERT_MODES
        for task in tasks:
            rec = rt.store.vehicles.get(task.vehicle_id)
            if rec is None:
                continue
            preds = assemble(task, ml, t, rt.ml.model_version)
            self._update_record(rec, task, preds, t)
            for ev in self._incidents(rec, task, preds, t, can_alert):
                rt.emit(*ev)
            rec.open_incident_id = rt.book.active.get(rec.vehicle_id)

    def _update_record(
        self, rec: VehicleRecord, task: VehicleTask, preds: list[VisitPrediction], t: datetime
    ) -> None:
        rec.current_dev_s = round(task.cur_dev, 1) if task.cur_dev is not None else None
        rec.next_stop = stop_ref(task.next_row) if task.next_row is not None else None
        rec.arrivals.update(task.arrivals)
        if task.cur_dev is not None:
            rec.dev_series.append((t, task.cur_dev))
        rec.prediction = preds[0].prediction if preds else None
        rec.window_preds = {vp.visit.visit_id: vp.prediction.predicted_delay_s for vp in preds}
        if rec.dirty_wall is None:
            rec.dirty_wall = self.rt.wall()
        journal = self.rt.journal
        if preds and preds[0].prediction.horizon_ok and not task.stale:
            first = preds[0]
            journal.record(rec.vehicle_id, first.visit.visit_id, first.visit.trip,
                           first.prediction.predicted_delay_s)  # fmt: skip
        journal.on_arrivals(rec.vehicle_id, task.arrivals)

    def _incidents(
        self,
        rec: VehicleRecord,
        task: VehicleTask,
        preds: list[VisitPrediction],
        t: datetime,
        can_alert: bool,
    ) -> list[Event]:
        """Resolve / update активного инцидента или открытие нового на первом красном."""
        book, vid = self.rt.book, rec.vehicle_id
        events = []
        if (ev := book.settle(vid, rec.arrivals, task.cur_dev, t)) is not None:
            events.append(ev)
        active = book.active.get(vid)
        if active is not None:
            target = book.meta[active].visit_id
            same = next((vp for vp in preds if vp.visit.visit_id == target), None)
            if same is not None and (ev := book.update(active, same.prediction, t)):
                events.append(ev)
            return events
        if not can_alert or task.stale or not task.ready:
            return events
        now = self.rt.sim_now()
        red = next((vp for vp in preds if book.can_open(vid, vp, now)), None)
        if red is not None:
            seg = target_segment(self.rt.static.plans[rec.tr_id], rec.tr_id,
                                 red.prediction.target_stop)  # fmt: skip
            info = {"vehicle_id": vid, "tr_id": str(rec.tr_id), "route_name": rec.route_name}
            events.append(book.open(info, red, seg, t))
        return events
