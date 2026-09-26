"""Состояние процесса бэкенда: связывает часы, state, инциденты, ingest, ML и WS.

Один объект :class:`Runtime` живёт в ``app.state.rt``. Всё, что зависит от времени,
берёт настенные часы из ``self.wall`` — в тестах их подменяют.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx

from transit_core import schemas as S

from .alerts.engine import IncidentBook
from .alerts.quality import QualityJournal, lead_ok_share, load_offline
from .config import Settings
from .degrade import ModeInputs, compute_mode, packet_age
from .ingest.stats import DropOldestQueue, IngestStats
from .journal_db import JournalDb
from .metrics import Metrics
from .ml_client import FALLBACK_VERSION, MlClient
from .state.clock import SessionUpdate, SimClock
from .state.static import StaticData, load_static, missing_data
from .state.store import FleetStore, VehicleRecord
from .state.views import vehicle_state

log = logging.getLogger(__name__)
UNKNOWN_STALE_WALL_S = 30.0
RISKS = ("green", "yellow", "red", "early", "none")


class Runtime:
    """Всё изменяемое состояние сервиса."""

    def __init__(
        self,
        settings: Settings,
        static: StaticData | None = None,
        wall: Callable[[], float] = time.monotonic,
        ml_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.wall = wall
        self.data_error = None if static else missing_data(settings.data_dir)
        if static is None and self.data_error is None:
            static = self._load(settings)
        self.static = static
        self.store = FleetStore(static, settings.history_min) if static else None
        self.clock = SimClock()
        self.book = IncidentBook()
        self.journal = QualityJournal(load_offline(settings.models_dir))
        self.stats = IngestStats()
        self.queue = DropOldestQueue(settings.queue_size, self.stats)
        self.metrics = Metrics()
        self.ml = MlClient(settings.ml_url, settings.ml_timeout_s, settings.ml_fail_threshold,
                           settings.ml_open_s, now=wall, transport=ml_transport)  # fmt: skip
        self.typical: dict[str, float] = {}  # типичная скорость перегонов, км/ч
        self.session_wall = wall()
        self.session_epoch = 0
        self.ingest_listening = False
        self.listeners: list[Callable[[str, object], None]] = [self._journal_event]
        self.db = JournalDb(settings.db_path)

    def _load(self, settings: Settings) -> StaticData | None:
        try:
            return load_static(settings.data_dir)
        except Exception as exc:  # noqa: BLE001 — сервис должен подняться и объяснить проблему
            log.exception("Не удалось загрузить данные")
            self.data_error = f"Ошибка чтения данных из {settings.data_dir}: {exc}"
            return None

    def emit(self, kind: str, payload: object = None) -> None:
        """Событие для WS-хаба (``session``, ``incident.*``, ``pass``)."""
        for fn in self.listeners:
            fn(kind, payload)

    def _journal_event(self, kind: str, payload: object) -> None:
        if kind.startswith("incident.") and isinstance(payload, S.Incident):
            self.db.incident(self.clock.session_id or "", kind, payload, payload.updated_at)

    def sim_now(self) -> datetime:
        return self.clock.now(self.wall())

    def apply_session(self, upd: SessionUpdate) -> bool:
        """Сообщение replayer'а о сессии; новая сессия очищает state и инциденты."""
        is_new = self.clock.apply(upd, self.wall())
        if is_new:
            self._reset_session()
        self.emit("session", is_new)
        return is_new

    def start_auto_session(self, et: datetime) -> None:
        """Пакеты без replayer'а: сессия создаётся сама, часы идут за пакетами."""
        self.clock.start_auto(et, self.wall())
        self._reset_session(drop_queue=False)  # в очереди — кадры этого же потока
        self.emit("session", True)

    def _reset_session(self, drop_queue: bool = True) -> None:
        if self.store is not None:
            self.store.clear()
        self.book.clear()
        self.journal.clear()
        if drop_queue:
            self.queue.clear()
        self.session_wall = self.wall()
        self.session_epoch += 1

    def is_stale(self, rec: VehicleRecord, now: datetime, wall: float) -> bool:
        """Давно нет данных: для неопознанных — по настенным часам, иначе по сим-времени."""
        if rec.kind == "unknown":
            return wall - rec.last_wall > UNKNOWN_STALE_WALL_S
        if rec.last_et is None:
            return True
        return (now - rec.last_et).total_seconds() > self.settings.stale_after_sim_s

    def is_warming(self, rec: VehicleRecord) -> bool:
        return rec.kind == "scheduled" and rec.history_min() < self.settings.warmup_min

    def vehicle_states(self) -> dict[str, S.VehicleState]:
        """Все ТС на карте."""
        if self.store is None:
            return {}
        now, wall = self.sim_now(), self.wall()
        return {
            vid: vehicle_state(rec, self.is_stale(rec, now, wall), self.is_warming(rec))
            for vid, rec in self.store.vehicles.items()
        }

    def sweep(self, mode: S.StreamMode) -> None:
        """Убирает с карты ТС, от которых давно нет данных (кроме режима DEGRADED).

        Для известных бортов «давно» отсчитывается от самого свежего пакета флота, а не
        от сим-часов: при обрыве всего потока ТС остаются на карте (stale), а не исчезают.
        """
        if self.store is None or mode == "DEGRADED":
            return
        wall = self.wall()
        gone = timedelta(seconds=self.settings.remove_after_sim_s)
        known = [r.last_et for r in self.store.vehicles.values()
                 if r.kind != "unknown" and r.last_et is not None]  # fmt: skip
        newest = max(known, default=None)
        for vid, rec in list(self.store.vehicles.items()):
            if rec.kind == "unknown":
                old = wall - rec.last_wall > self.settings.unknown_remove_wall_s
            else:
                old = rec.last_et is not None and newest - rec.last_et > gone
            if old and vid not in self.book.active:
                self.store.remove(vid)

    def mode(self) -> tuple[S.StreamMode, str | None]:
        """Режим системы и пояснение для баннера."""
        return compute_mode(self.clock, self._mode_inputs())

    def _mode_inputs(self) -> ModeInputs:
        recs = [r for r in (self.store.vehicles.values() if self.store else ())
                if r.kind == "scheduled"]  # fmt: skip
        return ModeInputs(
            wall=self.wall(),
            last_packet_wall=self.stats.last_packet_wall,
            session_wall=self.session_wall,
            all_warming=bool(recs) and all(self.is_warming(r) for r in recs),
            degraded_after_s=self.settings.degraded_after_wall_s,
        )

    def system_status(self) -> S.SystemStatus:
        """Статус для баннеров дашборда."""
        mode, reason = self.mode()
        age = packet_age(self._mode_inputs())
        ml_mode = "fallback" if mode == "DEGRADED" else self.ml.mode()
        return S.SystemStatus(
            mode=mode,
            reason=reason,
            last_packet_age_s=round(age, 1) if age is not None else None,
            units_connected=self.stats.connections,
            ml_status=self.ml.status,
            model_version=self.ml.model_version if ml_mode == "ml" else FALLBACK_VERSION,
            model_mode=ml_mode,
        )

    def kpis(self, states: dict[str, S.VehicleState] | None = None) -> S.Kpis:
        """Сводка для верхней полосы дашборда."""
        states = self.vehicle_states() if states is None else states
        by_risk = {k: 0 for k in RISKS}
        for s in states.values():
            by_risk[s.risk_level] += 1
        preds = [s.prediction.predicted_delay_s for s in states.values() if s.prediction]
        unknown = sum(s.kind == "unknown" for s in states.values())
        known = len(self.static.unit_map) if self.static else 0
        return S.Kpis(
            vehicles_online=sum(not s.stale for s in states.values()),
            vehicles_total=known + unknown,
            by_risk=S.RiskCounts(**by_risk),
            incidents_open=self.book.open_count(),
            avg_predicted_delay_s=round(sum(preds) / len(preds), 1) if preds else None,
            lead_ok_share=lead_ok_share(self.book),
            e2e_latency_ms_p95=self.metrics.windows["e2e"].p95(),
            ml_latency_ms_p95=self.metrics.windows["ml"].p95(),
            ingest_pps=self.stats.pps(self.wall()),
        )

    def ack(self, inc_id: str, req: S.AckRequest) -> S.Incident | None:
        """Реакция диспетчера; событие уходит в WS сразу."""
        ev = self.book.ack(inc_id, req, self.sim_now())
        if ev is None:
            return None
        self.emit(*ev)
        return ev[1]
