"""Метрики производительности: Prometheus (``/metrics``) и p50/p95/max для API.

Каждая латентность пишется одновременно в гистограмму Prometheus и в скользящее
окно последних измерений — из окна считается ``/api/v1/metrics/summary``.
"""

from __future__ import annotations

from collections import deque

import numpy as np
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from transit_core import schemas as S

WINDOW = 2000
LAT_BUCKETS = (0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)


class LatencyWindow:
    """Скользящее окно латентностей в миллисекундах."""

    def __init__(self, size: int = WINDOW) -> None:
        self._values: deque[float] = deque(maxlen=size)

    def add(self, ms: float) -> None:
        self._values.append(float(ms))

    def stats(self) -> S.LatencyStats:
        """p50/p95/max по окну; ``None``, пока измерений нет."""
        if not self._values:
            return S.LatencyStats(p50_ms=None, p95_ms=None, max_ms=None)
        arr = np.fromiter(self._values, dtype=float)
        p50, p95 = np.percentile(arr, [50, 95])
        return S.LatencyStats(
            p50_ms=round(float(p50), 2), p95_ms=round(float(p95), 2), max_ms=round(arr.max(), 2)
        )

    def p95(self) -> float | None:
        return self.stats().p95_ms


class Metrics:
    """Набор метрик сервиса (свой реестр — удобно для тестов)."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.windows = {
            name: LatencyWindow() for name in ("ingest_to_state", "pass_to_ws", "ml", "e2e")
        }
        self._hist = {
            "ingest_to_state": Histogram(
                "tp_ingest_to_state_ms", "Приём NDTP → запись в state, мс", buckets=LAT_BUCKETS,
                registry=r,
            ),
            "pass_to_ws": Histogram(
                "tp_pass_to_ws_ms", "Проход прогнозов → отправка в WS, мс", buckets=LAT_BUCKETS,
                registry=r,
            ),
            "ml": Histogram(
                "tp_ml_batch_ms", "Латентность батча ML, мс", buckets=LAT_BUCKETS, registry=r
            ),
            "e2e": Histogram(
                "tp_ingest_to_ws_ms", "Пакет → WS-дельта с этим ТС, мс", buckets=LAT_BUCKETS,
                registry=r,
            ),
        }  # fmt: skip
        self.packets = Counter("tp_ingest_packets", "Принято NDTP-кадров", registry=r)
        self.drops = Counter("tp_ingest_dropped", "Выброшено при переполнении очереди", registry=r)
        self.queue_lag = Gauge("tp_ingest_queue_lag", "Кадров в очереди ingest", registry=r)
        self.batch_size = Histogram(
            "tp_ml_batch_size", "Размер батча ML", buckets=(1, 5, 10, 25, 50, 100, 250, 500),
            registry=r,
        )  # fmt: skip
        self.ws_clients = Gauge("tp_ws_clients", "Подключённых WS-клиентов", registry=r)

    def observe(self, name: str, ms: float) -> None:
        """Пишет латентность ``name`` (мс) в гистограмму и окно."""
        self.windows[name].add(ms)
        self._hist[name].observe(ms)

    def stats(self, name: str) -> S.LatencyStats:
        return self.windows[name].stats()
