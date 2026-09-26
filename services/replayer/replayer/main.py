"""HTTP-сервис replayer (порт 8090): ``/health``, ``/status``, ``/control``.

Запуск локально::

    DATA_DIR=data/raw BACKEND_NDTP=127.0.0.1:9201 BACKEND_HTTP=http://127.0.0.1:8000 \\
      PYTHONPATH=services/replayer uv run uvicorn replayer.main:app --port 8090
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from replayer.config import Settings
from replayer.engine import ReplayEngine
from replayer.session import SessionReporter
from replayer.source import load_track
from transit_core.schemas import ReplayControl

log = logging.getLogger("replayer")


def build_engine(settings: Settings) -> ReplayEngine:
    """Читает историю и собирает движок (без запуска)."""
    track = load_track(settings.traffic_csv)
    log.info("история: %d строк, %d юнитов", len(track), len(track.units))
    return ReplayEngine(settings, track, SessionReporter(settings.backend_http))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Поднимает движок при старте приложения и гасит при остановке."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    settings = Settings.from_env()
    engine = build_engine(settings)
    await engine.start()
    app.state.engine = engine
    try:
        yield
    finally:
        await engine.stop()


app = FastAPI(title="Transit Pulse replayer", version="0.1.0", lifespan=lifespan)


def _engine(request: Request) -> ReplayEngine:
    """Движок текущего приложения."""
    return request.app.state.engine


@app.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Живость: движок поднят, история загружена."""
    engine = _engine(request)
    return {"status": "ok", "rows": len(engine.track), "state": engine.clock.state}


@app.get("/status")
def status(request: Request) -> dict[str, Any]:
    """Сессия, сим-время, скорость, соединения и счётчики пакетов."""
    return _engine(request).status()


@app.post("/control")
def control(body: ReplayControl, request: Request) -> dict[str, Any]:
    """Управление воспроизведением: start / pause / resume / speed / seek."""
    engine = _engine(request)
    try:
        engine.control(body.action, speed=body.speed, seek_to=body.seek_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return engine.status()
