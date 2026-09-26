"""WebSocket ``/ws/v1/stream``: snapshot, дельты ТС, инциденты, KPI, статус, ping.

* ``snapshot`` — при подключении и при смене сессии;
* ``vehicles.delta`` — раз в секунду (и сразу после прохода прогнозов), только изменившиеся;
* ``incident.*`` — сразу по событию;
* ``kpis`` — раз в 2 с; ``system.status`` — при смене режима; ``ping`` — раз в 10 с.

Каждое сообщение собирается контрактной моделью (``schema_version/session_id/sim_time``).
Медленный клиент с переполненной очередью отключается — он переподключится и получит
свежий snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from transit_core import schemas as S

from .state.clock import NO_SESSION
from .state.timefmt import fmt

if TYPE_CHECKING:
    from .runtime import Runtime

log = logging.getLogger(__name__)
CLIENT_QUEUE = 1000
DELTA_S, KPIS_EVERY, PING_EVERY = 1.0, 2, 10
INCIDENT_MODELS = ("incident.opened", "incident.updated", "incident.resolved")


class WsHub:
    """Рассылка сообщений всем подключённым клиентам."""

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.clients: set[asyncio.Queue] = set()
        self.last_sent: dict[str, S.VehicleState] = {}
        self._status_key: tuple | None = None
        rt.listeners.append(self.on_event)

    # --------------------------------------------------------------- конверты
    def _env(self) -> dict:
        return {"session_id": self.rt.clock.session_id or NO_SESSION,
                "sim_time": fmt(self.rt.sim_now())}  # fmt: skip

    def message(self, kind: str, data) -> str:
        """JSON сообщения ``kind`` через контрактную модель."""
        env = self._env()
        if kind == "snapshot":
            return S.WsSnapshot(**env, data=data).model_dump_json()
        if kind == "vehicles.delta":
            return S.WsVehiclesDelta(**env, data=data).model_dump_json()
        if kind in INCIDENT_MODELS:
            return S.WsIncident(type=kind, **env, data=data).model_dump_json()
        if kind == "kpis":
            return S.WsKpis(**env, data=data).model_dump_json()
        if kind == "system.status":
            return S.WsSystemStatus(**env, data=data).model_dump_json()
        return S.WsPing(**env).model_dump_json()

    def snapshot(self) -> str:
        """Полное состояние (ТС — в том виде, в каком их уже видят остальные клиенты)."""
        data = S.SnapshotData(
            vehicles=list(self.last_sent.values()),
            incidents=list(self.rt.book.incidents.values()),
            kpis=self.rt.kpis(self.last_sent),
            status=self.rt.system_status(),
        )
        return self.message("snapshot", data)

    def broadcast(self, text: str) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:  # клиент не успевает: закрываем, он переподключится
                self.clients.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)

    # ---------------------------------------------------------------- события
    def on_event(self, kind: str, payload) -> None:
        """Слушатель событий :class:`Runtime`."""
        if kind == "session":
            if payload:  # новая сессия: всё заново
                self.last_sent.clear()
                self.broadcast(self.snapshot())
            self.check_status(force=True)
        elif kind in INCIDENT_MODELS:
            self.broadcast(self.message(kind, payload))
        elif kind == "pass":
            self.flush_delta(pass_t0=payload)

    def flush_delta(self, pass_t0: float | None = None) -> None:
        """Отправляет изменившиеся и убранные ТС."""
        states = self.rt.vehicle_states()
        changed = [s for vid, s in states.items() if self.last_sent.get(vid) != s]
        removed = [vid for vid in self.last_sent if vid not in states]
        if changed or removed:
            data = S.VehiclesDeltaData(vehicles=changed, removed=removed)
            self.broadcast(self.message("vehicles.delta", data))
            self._observe_e2e(changed)
        self.last_sent = states
        if pass_t0 is not None:
            self.rt.metrics.observe("pass_to_ws", (time.perf_counter() - pass_t0) * 1000)

    def _observe_e2e(self, changed: list[S.VehicleState]) -> None:
        store, wall = self.rt.store, self.rt.wall()
        for s in changed:
            rec = store.vehicles.get(s.vehicle_id) if store else None
            if rec is not None and rec.dirty_wall is not None:
                self.rt.metrics.observe("e2e", max(wall - rec.dirty_wall, 0.0) * 1000)
                rec.dirty_wall = None

    def check_status(self, force: bool = False) -> None:
        """``system.status`` при смене режима, ML или числа подключений."""
        st = self.rt.system_status()
        key = (st.mode, st.ml_status, st.model_mode, st.model_version, st.units_connected)
        if force or key != self._status_key:
            self._status_key = key
            self.broadcast(self.message("system.status", st))

    # ------------------------------------------------------------ фоновый цикл
    async def run(self) -> None:
        """Периодические сообщения: дельты 1 Гц, KPI 2 с, статус, ping 10 с."""
        tick = 0
        while True:
            await asyncio.sleep(DELTA_S)
            tick += 1
            try:
                self.rt.sweep(self.rt.mode()[0])
                self.flush_delta()
                self.check_status()
                if tick % KPIS_EVERY == 0:
                    self.broadcast(self.message("kpis", self.rt.kpis(self.last_sent)))
                if tick % PING_EVERY == 0:
                    self.broadcast(self.message("ping", None))
                self.rt.metrics.ws_clients.set(len(self.clients))
            except Exception:  # noqa: BLE001
                log.exception("Ошибка цикла WS")

    async def serve(self, ws: WebSocket) -> None:
        """Обслуживает одного клиента: snapshot, затем поток сообщений."""
        await ws.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE)
        q.put_nowait(self.snapshot())
        self.clients.add(q)
        sender = asyncio.create_task(self._send_loop(ws, q))
        receiver = asyncio.create_task(self._recv_loop(ws))
        try:
            await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.clients.discard(q)
            for task in (sender, receiver):
                task.cancel()
            with contextlib.suppress(Exception):
                await ws.close()

    @staticmethod
    async def _send_loop(ws: WebSocket, q: asyncio.Queue) -> None:
        while (text := await q.get()) is not None:
            await ws.send_text(text)

    @staticmethod
    async def _recv_loop(ws: WebSocket) -> None:
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            while True:
                await ws.receive_text()


router = APIRouter()


@router.websocket("/ws/v1/stream")
async def stream(ws: WebSocket) -> None:
    """Поток реального времени для дашборда (типы — ``WsMessage`` контракта)."""
    await ws.app.state.hub.serve(ws)
