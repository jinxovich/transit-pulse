"""FastAPI-приложение бэкенда Transit Pulse.

Запуск локально::

    uv run uvicorn --app-dir services/backend app.main:app --port 8000

Порты: HTTP ``8000`` (REST ``/api/v1``, WS ``/ws/v1/stream``, Swagger ``/docs``,
Prometheus ``/metrics``) и NDTP TCP ``9201``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .api import fleet, incidents, system
from .config import VERSION, Settings
from .ingest.server import IngestServer
from .ingest.worker import run_worker
from .internal import router as internal_router
from .pipeline.scheduler import PipelineRunner
from .runtime import Runtime
from .state.static import load_typical_speeds
from .ws import WsHub
from .ws import router as ws_router

log = logging.getLogger(__name__)
ML_HEALTH_S = 10.0

DESCRIPTION = """
Бэкенд предиктора задержек: приём NDTP (TCP 9201) → state → прогноз на 10–15 минут
(ML-сервис с circuit breaker и эвристикой) → инциденты с причинами и рекомендациями →
REST `/api/v1` и WebSocket `/ws/v1/stream` для дашборда диспетчера.

Время везде — naive ISO «времени датасета» (`2026-01-06T07:14:00`), задержка в секундах,
`+` — опоздание. Контракт: `packages/transit_core/transit_core/schemas.py`.
"""


async def _ml_health_loop(rt: Runtime) -> None:
    while True:
        await rt.ml.poll_health()
        await asyncio.sleep(ML_HEALTH_S)


async def _start_ingest(app: FastAPI, rt: Runtime) -> None:
    server = IngestServer(rt.stats, rt.queue, rt.wall)
    try:
        app.state.ndtp_port = await server.start(rt.settings.ndtp_host, rt.settings.ndtp_port)
        rt.ingest_listening = True
        app.state.ingest = server
    except OSError:
        log.exception("Не удалось открыть NDTP-порт %s", rt.settings.ndtp_port)


async def _load_typical(rt: Runtime) -> None:
    """Типичные скорости перегонов считаются в фоне: сервис готов, не дожидаясь их."""
    if rt.static is None:
        return
    try:
        rt.typical = await asyncio.to_thread(load_typical_speeds, rt.settings.data_dir, rt.static)
        log.info("Типичные скорости: %d перегонов", len(rt.typical))
    except Exception:  # noqa: BLE001 — без них просто нет speed_ratio
        log.exception("Не удалось посчитать типичные скорости перегонов")


def _background(app: FastAPI, rt: Runtime) -> list[asyncio.Task]:
    coros = [run_worker(rt), app.state.pipeline.loop(), app.state.hub.run(), _ml_health_loop(rt),
             _load_typical(rt)]  # fmt: skip
    return [asyncio.create_task(c) for c in coros]


def create_app(runtime: Runtime | None = None, background: bool = True) -> FastAPI:
    """Собирает приложение; ``runtime`` и ``background`` подменяются в тестах."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = runtime or Runtime(Settings.from_env())
        app.state.rt = rt
        app.state.hub = WsHub(rt)
        app.state.pipeline = PipelineRunner(rt)
        tasks: list[asyncio.Task] = []
        if background:
            if rt.settings.ndtp_enabled:
                await _start_ingest(app, rt)
            tasks = _background(app, rt)
        yield
        for task in tasks:
            task.cancel()
        if getattr(app.state, "ingest", None) is not None:
            await app.state.ingest.stop()
        await rt.ml.close()
        rt.db.close()

    app = FastAPI(
        title="Transit Pulse — backend",
        version=VERSION,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    settings = runtime.settings if runtime else Settings.from_env()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (system.router, fleet.router, incidents.router, internal_router, ws_router):
        app.include_router(router)

    @app.get("/metrics", tags=["Система"], summary="Метрики Prometheus")
    def metrics(request: Request) -> Response:
        """Латентности ingest→state, проход→WS, батч ML; счётчики пакетов и потерь."""
        data = generate_latest(request.app.state.rt.metrics.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
