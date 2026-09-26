"""Клиент ML-сервиса (INTERFACES §4) с circuit breaker и эвристикой-заменой.

3 ошибки подряд → breaker открыт на 15 с: запросы в ml не идут, прогноз считается
эвристикой ``delay = 0.7·cur_dev`` (``model_mode="fallback"``). По истечении паузы
одна пробная попытка: успех закрывает breaker, ошибка снова открывает.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from transit_core import schemas as S

log = logging.getLogger(__name__)
FALLBACK_VERSION = "fallback-0.7cur_dev"
FALLBACK_K = 0.7
FALLBACK_MAE_S = 60.0
LATE_S = 120.0
YELLOW_S = 60.0


@dataclass(frozen=True)
class MlItem:
    """Прогноз для одного визита."""

    id: str
    delay_s: float
    q10: float
    q90: float
    p_late: float
    expected_abs_error_s: float
    contributions: list[dict] = field(default_factory=list)


def fallback_item(item_id: str, cur_dev: float | None) -> MlItem:
    """Эвристика без ML: ``0.7·cur_dev``, p_late по порогам риска."""
    d = FALLBACK_K * (cur_dev or 0.0)
    p_late = 0.8 if d > LATE_S else (0.35 if d >= YELLOW_S else 0.05)
    spread = 40.0 + 0.3 * abs(d)
    return MlItem(item_id, round(d, 1), round(d - spread, 1), round(d + spread, 1), p_late,
                  FALLBACK_MAE_S)  # fmt: skip


class CircuitBreaker:
    """Простой breaker: closed → open (после N ошибок) → half-open (по таймеру)."""

    def __init__(self, threshold: int, open_s: float, now: Callable[[], float]) -> None:
        self.threshold, self.open_s, self.now = threshold, open_s, now
        self.failures = 0
        self.open_until: float | None = None

    @property
    def is_open(self) -> bool:
        return self.open_until is not None and self.now() < self.open_until

    def allow(self) -> bool:
        """Можно ли сейчас звать ml."""
        return not self.is_open

    def success(self) -> None:
        self.failures, self.open_until = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.open_until = self.now() + self.open_s


def _clean(features: dict[str, float]) -> dict[str, float | None]:
    """NaN → null: JSON не знает NaN, ml трактует null как пропуск."""
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in features.items()}


def _parse_item(raw: dict) -> MlItem | None:
    """Прогноз визита; ``None``, если в числах NaN/inf (для визита сработает эвристика)."""
    nums = [raw.get(k) for k in ("delay_s", "q10", "q90", "p_late", "expected_abs_error_s")]
    if any(v is not None and not math.isfinite(float(v)) for v in nums):
        return None
    d = float(raw["delay_s"])
    return MlItem(
        id=str(raw["id"]),
        delay_s=d,
        q10=float(raw.get("q10", d)),
        q90=float(raw.get("q90", d)),
        p_late=min(max(float(raw.get("p_late", 0.0)), 0.0), 1.0),
        expected_abs_error_s=float(raw.get("expected_abs_error_s", FALLBACK_MAE_S)),
        contributions=list(raw.get("contributions") or []),
    )


class MlClient:
    """Асинхронный клиент ``POST /predict`` с breaker'ом."""

    def __init__(
        self,
        url: str,
        timeout_s: float = 1.0,
        fail_threshold: int = 3,
        open_s: float = 15.0,
        now: Callable[[], float] = time.monotonic,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.breaker = CircuitBreaker(fail_threshold, open_s, now)
        self.http = httpx.AsyncClient(timeout=timeout_s, transport=transport)
        self.model_version = FALLBACK_VERSION
        self.health_ok = False
        self.last_latency_ms: float | None = None

    async def close(self) -> None:
        await self.http.aclose()

    @property
    def status(self) -> str:
        """``ok`` / ``degraded`` / ``down`` для ``SystemStatus.ml_status``."""
        if self.breaker.is_open:
            return "down"
        if self.breaker.failures or not self.health_ok:
            return "degraded"
        return "ok"

    async def predict(self, items: list[tuple[str, dict]]) -> dict[str, MlItem] | None:
        """Прогнозы по ``[(id, features)]``; ``None`` — ml недоступен, нужна эвристика."""
        if not items or not self.breaker.allow():
            return None
        body = {"model": "stream", "items": [{"id": i, "features": _clean(f)} for i, f in items]}
        t0 = time.perf_counter()
        try:
            resp = await self.http.post(f"{self.url}/predict", json=body)
            resp.raise_for_status()
            data = resp.json()
            parsed = (_parse_item(raw) for raw in data["items"])
            out = {it.id: it for it in parsed if it is not None}
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            log.warning("ml /predict не ответил: %s", exc)
            self.breaker.failure()
            return None
        self.last_latency_ms = (time.perf_counter() - t0) * 1000
        self.model_version = str(data.get("model_version", self.model_version))
        self.breaker.success()
        self.health_ok = True
        return out

    async def poll_health(self) -> None:
        """Фоновая проверка ``GET /health`` ml (для /health бэкенда и версии модели)."""
        try:
            resp = await self.http.get(f"{self.url}/health")
            resp.raise_for_status()
            data = resp.json()
            self.health_ok = data.get("status", "ok") == "ok"
            self.model_version = str(data.get("model_version", self.model_version))
        except Exception:  # noqa: BLE001 — фоновая проверка не должна умирать
            self.health_ok = False

    def mode(self) -> S.ModelMode:
        return "fallback" if self.breaker.is_open or not self.health_ok else "ml"
