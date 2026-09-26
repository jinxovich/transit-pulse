"""REST: здоровье, конфиг, сим-часы, управление воспроизведением, метрики и ingest."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from transit_core import schemas as S

from ..config import VERSION
from ..pipeline.adapters import IMPL
from ..runtime import Runtime
from .deps import Rt

router = APIRouter(prefix="/api/v1", tags=["Система"])
REPLAYER_TIMEOUT_S = 3.0


def health_of(rt: Runtime) -> S.Health:
    """Сводка проверок: данные, ingest, ML, поток."""
    mode, _ = rt.mode()
    checks = {
        "data": rt.data_error or "ok",
        "ingest": f"ok: NDTP :{rt.settings.ndtp_port}" if rt.ingest_listening else "не слушает",
        "ml": rt.ml.status,
        "stream": mode,
        "pipeline": ", ".join(f"{k}={v}" for k, v in IMPL.items()),
    }
    if rt.data_error:
        status = "error"
    elif rt.ml.status != "ok" or mode == "DEGRADED" or not rt.ingest_listening:
        status = "degraded"
    else:
        status = "ok"
    return S.Health(status=status, version=VERSION, checks=checks)


@router.get("/health", response_model=S.Health, summary="Здоровье сервиса")
async def health(rt: Rt):
    """Быстрый отказ с понятной причиной (HTTP 503), если нет датасета; иначе статус
    проверок ML, ingest и потока. ``degraded`` не мешает работе — это подсказка."""
    h = health_of(rt)
    if h.status == "error":
        return JSONResponse(status_code=503, content=h.model_dump(mode="json"))
    return h


@router.get("/config", response_model=S.AppConfig, summary="Пороги, цвета, горизонт")
async def config(rt: Rt) -> S.AppConfig:
    """Конфигурация дашборда: пороги риска и цвета берутся только отсюда."""
    clock = rt.clock.contract(rt.wall())
    return S.AppConfig(sim_speed=clock.speed, session_id=clock.session_id)


@router.get("/sim/clock", response_model=S.SimClock, summary="Сим-часы")
async def sim_clock(rt: Rt) -> S.SimClock:
    """Текущее сим-время, сессия, скорость и состояние воспроизведения."""
    return rt.clock.contract(rt.wall())


def _clock_from(data: object, rt: Runtime) -> S.SimClock:
    fallback = rt.clock.contract(rt.wall())
    if not isinstance(data, dict):
        return fallback
    try:
        merged = {**fallback.model_dump(), **{k: data[k] for k in S.SimClock.model_fields
                                               if data.get(k) is not None}}  # fmt: skip
        return S.SimClock.model_validate(merged)
    except ValueError:
        return fallback


@router.post("/replay/control", response_model=S.SimClock, summary="Управление воспроизведением")
async def replay_control(body: S.ReplayControl, rt: Rt) -> S.SimClock:
    """Пауза, продолжение, скорость, перемотка — проксируется в replayer (``POST /control``)."""
    url = f"{rt.settings.replayer_url}/control"
    try:
        async with httpx.AsyncClient(timeout=REPLAYER_TIMEOUT_S) as client:
            resp = await client.post(url, json=body.model_dump(mode="json", exclude_none=True))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Replayer недоступен: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=f"Replayer: {resp.text[:300]}")
    try:
        return _clock_from(resp.json(), rt)
    except ValueError:
        return rt.clock.contract(rt.wall())


@router.get("/ingest/stats", response_model=S.IngestStats, summary="Приём NDTP")
async def ingest_stats(rt: Rt) -> S.IngestStats:
    """Пакеты в секунду, ошибки CRC/разбора, соединения и последний пакет (hex + поля)."""
    return rt.stats.contract(rt.wall())


@router.get("/metrics/summary", response_model=S.MetricsSummary, summary="Производительность")
async def metrics_summary(rt: Rt) -> S.MetricsSummary:
    """KPI и латентности: ingest→state, проход прогнозов→WS, батч ML (p50/p95/max)."""
    m = rt.metrics
    return S.MetricsSummary(
        kpis=rt.kpis(),
        ingest_to_state=m.stats("ingest_to_state"),
        pass_to_ws=m.stats("pass_to_ws"),
        ml_batch=m.stats("ml"),
        queue_lag=rt.queue.qsize(),
        dropped_packets=rt.stats.dropped,
    )


@router.get("/metrics/quality", response_model=S.QualityMetrics, summary="Качество прогнозов")
async def metrics_quality(rt: Rt) -> S.QualityMetrics:
    """Онлайн-MAE по виртуальному факту, упреждение алертов, precision/recall, офлайн-CV."""
    return rt.journal.contract(rt.book)
