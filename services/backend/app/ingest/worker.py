"""Потребитель очереди ingest: кадр Nav00 → точка в кольцевом буфере ТС.

Неопознанный борт (нет в справочнике или время вне дня датасета) попадает на карту
как ``u:<unit_id>``, но часы не двигает и прогнозов не получает. Точки известных
бортов подтягивают сим-часы; «хвосты» чужой сессии (сильно впереди часов) и
слишком старые точки отбрасываются.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..state.timefmt import from_epoch
from .codec import Frame

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)


def _point(frame: Frame) -> tuple:
    nav = frame.nav
    et = from_epoch(nav.timestamp)
    if not nav.valid:
        return (et, 0.0, 0.0, 0.0, 0.0, False)  # как ndtp_normalize: нули у невалидных
    return (et, nav.lon, nav.lat, float(nav.speed_kmh), float(nav.course), True)


def apply_frame(rt: Runtime, recv_wall: float, frame: Frame) -> bool:
    """Записывает кадр в state; ``False`` — кадр отброшен (нет данных/чужая сессия)."""
    rt.metrics.packets.inc()
    store = rt.store
    if store is None or frame.nav is None:
        return False
    point = _point(frame)
    et = point[0]
    _, kind, _ = store.classify(frame.unit_id, et)
    if kind == "unknown":
        rt.stats.unknown_units.add(frame.unit_id)
    else:
        if not rt.clock.has_session:
            rt.start_auto_session(et)
        wall = rt.wall()
        too_old = rt.clock.now(wall) - et > store.history
        if too_old or not rt.clock.accepts(et, wall):
            return False
        rt.clock.observe(et, wall)
    rec = store.record(frame.unit_id, et)
    store.add(rec, point, rt.wall())
    rt.metrics.observe("ingest_to_state", max(rt.wall() - recv_wall, 0.0) * 1000)
    return True


async def run_worker(rt: Runtime) -> None:
    """Бесконечный цикл разбора очереди ingest."""
    while True:
        recv_wall, frame = await rt.queue.get()
        rt.metrics.queue_lag.set(rt.queue.qsize())
        try:
            apply_frame(rt, recv_wall, frame)
        except Exception:  # noqa: BLE001 — один битый кадр не останавливает поток
            log.exception("Ошибка записи кадра unit=%s", frame.unit_id)
