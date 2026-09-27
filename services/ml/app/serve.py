"""ML-сервис Transit Pulse (FastAPI, порт 8001): батч-прогноз задержки по признакам as-of T.

Запуск локально::

    MODELS_DIR=models uv run uvicorn services.ml.app.serve:app --port 8001

После дообучения (``services.ml.app.train``) новые модели подхватываются без рестарта:
``POST /model/reload``.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from fastapi import Body, FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from services.ml.app.inference import Registry, load_registry, predict, to_matrix
from services.ml.app.openapi_examples import PREDICT_EXAMPLES

ROOT = Path(__file__).resolve().parents[3]
WARMUP_BATCH = 8

LATENCY = Histogram("ml_predict_latency_seconds", "Латентность /predict, с",
                    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0))
BATCH = Histogram("ml_predict_batch_size", "Размер батча /predict",
                  buckets=(1, 5, 10, 30, 50, 100, 300, 1000))
REQUESTS = Counter("ml_predict_requests_total", "Запросы /predict", ["model"])


class Item(BaseModel):
    """Одна прогнозная точка."""

    id: str = Field(..., description="Ключ точки, например `130072:53700172828` (ТС:визит)")
    features: dict[str, float | None] = Field(
        ..., description="Признаки из `transit_core.features.FEATURES`; отсутствующие → NaN")


class PredictRequest(BaseModel):
    """Батч точек для прогноза."""

    model: Literal["stream", "submission"] = Field(
        "stream", description="`stream` — cur_dev по GPS (онлайн), `submission` — подсказка "
                              "cur_dev_s из points.csv")
    items: list[Item] = Field(..., description="Точки прогноза")
    explain: bool = Field(True, description="Считать вклады признаков (точный SHAP CatBoost, "
                                            "~0.1–0.3 с на батч). false — только прогноз "
                                            "(~2 мс), contributions пустые")


class Contribution(BaseModel):
    feature: str = Field(..., description="Имя признака")
    contribution_s: float = Field(..., description="Вклад в прогноз, секунды (SHAP медианы; "
                                                   "у cur_dev — вместе с базой)")


class Prediction(BaseModel):
    id: str
    delay_s: float = Field(..., description="Прогноз задержки (медиана), с; + — опоздание")
    q10: float = Field(..., description="10-й перцентиль задержки, с")
    q90: float = Field(..., description="90-й перцентиль задержки, с")
    p_late: float = Field(..., description="Вероятность задержки > 120 с (изотоническая "
                                           "калибровка по OOF, если есть файл калибровки)")
    expected_abs_error_s: float = Field(..., description="Ожидаемая абсолютная ошибка, с "
                                                         "(полуширина q10–q90 × калибровка)")
    contributions: list[Contribution] = Field(..., description="Топ-5 вкладов по модулю")


class PredictResponse(BaseModel):
    model_version: str
    latency_ms: float = Field(..., description="Время инференса в сервисе, мс")
    items: list[Prediction]


STATE: dict[str, Registry] = {}
RELOAD_LOCK = threading.Lock()


class ReloadResponse(BaseModel):
    status: Literal["reloaded", "unchanged"] = Field(
        ..., description="`unchanged` — файлы моделей те же, что уже загружены")
    before: dict[str, str] = Field(..., description="Режим → отпечаток модели до перезагрузки")
    after: dict[str, str] = Field(..., description="Режим → отпечаток загруженной модели")
    load_ms: float = Field(..., description="Загрузка и прогрев новых моделей, мс")


def _models_dir() -> Path:
    return Path(os.environ.get("MODELS_DIR", ROOT / "models"))


def _fingerprints(reg: Registry | None) -> dict[str, str]:
    return {mode: lm.fingerprint for mode, lm in reg.models.items()} if reg else {}


def _warmup(reg: Registry) -> None:
    """Прогрев: фиктивный батч через каждую модель (JIT-кеши, SHAP-деревья)."""
    x = np.full((WARMUP_BATCH, len(reg.features)), np.nan)
    for lm in reg.models.values():
        predict(lm, x, reg.features)


@asynccontextmanager
async def lifespan(_: FastAPI):
    reg = load_registry(_models_dir())
    _warmup(reg)
    STATE["reg"] = reg
    yield
    STATE.clear()


app = FastAPI(title="Transit Pulse ML", version="2.0",
              description="Прогноз задержки ТС на остановке за 10–15 минут (CatBoost "
                          "MultiQuantile на остатке от текущего отклонения).",
              lifespan=lifespan)


def _registry() -> Registry:
    reg = STATE.get("reg")
    if reg is None:
        raise HTTPException(503, "модели ещё не загружены")
    return reg


@app.post("/predict", response_model=PredictResponse, summary="Батч-прогноз задержки")
def predict_endpoint(
    req: Annotated[PredictRequest, Body(openapi_examples=PREDICT_EXAMPLES)],
) -> PredictResponse:
    """Квантили задержки, вероятность опоздания > 120 с и топ-5 вкладов признаков."""
    reg = _registry()
    lm = reg.models.get(req.model)
    if lm is None:
        raise HTTPException(404, f"модель {req.model} не загружена")
    start = time.perf_counter()
    x = to_matrix([it.features for it in req.items], reg.features)
    preds = predict(lm, x, reg.features, with_contributions=req.explain) if len(x) else []
    elapsed = time.perf_counter() - start
    LATENCY.observe(elapsed)
    BATCH.observe(len(req.items))
    REQUESTS.labels(req.model).inc()
    items = [Prediction(id=it.id, **p) for it, p in zip(req.items, preds, strict=True)]
    return PredictResponse(model_version=lm.version, latency_ms=round(elapsed * 1000, 3),
                           items=items)


@app.get("/health", summary="Готовность сервиса")
def health() -> dict:
    """``ok``, если модели загружены и прогреты."""
    reg = _registry()
    first = next(iter(reg.models.values()))
    return {"status": "ok", "model_version": first.version,
            "models": sorted(m.version for m in reg.models.values())}


@app.get("/model/info", summary="Признаки, метрики и калибровка модели")
def model_info() -> dict:
    """Порядок признаков, метрики CV из ``models/metrics.json`` и сводка калибровки по режимам
    (масштабы интервала по корзинам lead, узлы изотоники p_late, Brier/ECE до→после);
    ``null`` — у режима нет ``calibration_{mode}.json``, работает общий ``interval_scale``."""
    reg = _registry()
    cal = {mode: lm.calibration.summary if lm.calibration else None
           for mode, lm in reg.models.items()}
    return {"features": reg.features, "metrics": reg.metrics, "calibration": cal,
            "fingerprints": _fingerprints(reg)}


@app.post("/model/reload", response_model=ReloadResponse,
          summary="Подхватить дообученные модели без рестарта")
def reload_models() -> ReloadResponse:
    """Читает ``MODELS_DIR`` заново, прогревает и атомарно подменяет модели.

    Пока новые модели загружаются, ``/predict`` отвечает старыми. Если загрузка не удалась
    (нет файлов, битая модель), продолжают работать прежние модели, ответ — 500 с причиной.
    """
    with RELOAD_LOCK:
        before = _fingerprints(STATE.get("reg"))
        start = time.perf_counter()
        try:
            reg = load_registry(_models_dir())
            _warmup(reg)
        except Exception as exc:
            raise HTTPException(500, f"модели не перезагружены, работают прежние: {exc}") from exc
        STATE["reg"] = reg
        after = _fingerprints(reg)
    return ReloadResponse(status="unchanged" if after == before else "reloaded", before=before,
                          after=after, load_ms=round((time.perf_counter() - start) * 1000, 1))


@app.get("/metrics", summary="Метрики Prometheus")
def metrics() -> Response:
    """Гистограммы латентности и размера батча, счётчик запросов."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
