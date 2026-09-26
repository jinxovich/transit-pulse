"""Качество прогнозов на потоке: онлайн-MAE, упреждение алертов, precision/recall.

Журнал хранит «первый в окне» прогноз ТС на каждую сим-минуту (как карточка ТС).
Когда по GPS фиксируется прибытие на целевую остановку, прогнозы по ней сверяются
с фактом. ``miss`` — фактическое опоздание > порога red на цели, по рейсу которой
инцидента не было.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path

from transit_core import schemas as S

from .engine import IncidentBook

log = logging.getLogger(__name__)
LEAD_BUCKETS = range(10, 16)
LEAD_OK_MIN = 10.0


def load_offline(models_dir: Path) -> S.OfflineMetrics:
    """Офлайн-метрики CV из ``models/metrics.json`` (если файла нет — нули)."""
    path = models_dir / "metrics.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return S.OfflineMetrics(cv_mae_baseline_s=0.0, cv_mae_model_s=0.0, improvement=0.0)
    src = data.get("offline", data) if isinstance(data, dict) else {}
    src = src if isinstance(src, dict) else {}
    base = _pick(src, "cv_mae_baseline_s", "mae_baseline", "test_mae_baseline", "baseline_mae")
    model = _pick(src, "cv_mae_model_s", "mae_model", "test_mae_model", "model_mae")
    impr = _pick(src, "improvement", "test_improvement")
    if impr == 0.0 and base > 0:
        impr = round(1 - model / base, 4)
    return S.OfflineMetrics(cv_mae_baseline_s=base, cv_mae_model_s=model, improvement=impr)


def _pick(d: dict, *keys: str) -> float:
    for k in keys:
        if isinstance(d.get(k), int | float):
            return float(d[k])
    return 0.0


class QualityJournal:
    """Журнал прогнозов сессии и их сверка с виртуальным фактом."""

    def __init__(self, offline: S.OfflineMetrics, th: S.Thresholds | None = None) -> None:
        self.offline = offline
        self.th = th or S.Thresholds()
        self.clear()

    def clear(self) -> None:
        self.pending: dict[tuple[str, str], list[float]] = defaultdict(list)
        self.trip_of: dict[tuple[str, str], int] = {}
        self.errors: list[float] = []
        self.evaluated: set[tuple[str, str]] = set()
        self.late_targets: set[tuple[str, int]] = set()

    def record(self, vehicle_id: str, visit_id: str, trip: int, predicted_s: float) -> None:
        """Запоминает прогноз по цели (только ``horizon_ok``)."""
        key = (vehicle_id, visit_id)
        if key not in self.evaluated:
            self.pending[key].append(predicted_s)
            self.trip_of[key] = trip

    def on_arrivals(self, vehicle_id: str, arrivals: dict[str, tuple]) -> None:
        """Сверяет прогнозы с фактами прибытия ТС."""
        for visit_id, (_, actual) in arrivals.items():
            key = (vehicle_id, visit_id)
            preds = self.pending.pop(key, None)
            if preds is None:
                continue
            self.evaluated.add(key)
            self.errors.extend(abs(p - actual) for p in preds)
            if actual > self.th.red_delay_s:
                self.late_targets.add((vehicle_id, self.trip_of[key]))

    def contract(self, book: IncidentBook) -> S.QualityMetrics:
        """Ответ ``GET /api/v1/metrics/quality``."""
        incs = list(book.incidents.values())
        outcomes = Counter(i.outcome for i in incs if i.status == "resolved")
        hits, fa = outcomes.get("hit", 0), outcomes.get("false_alarm", 0)
        misses = sum(1 for key in self.late_targets if key not in book.trips)
        leads = Counter(min(max(int(i.lead_min), LEAD_BUCKETS[0]), LEAD_BUCKETS[-1]) for i in incs)
        return S.QualityMetrics(
            online_mae_s=round(sum(self.errors) / len(self.errors), 1) if self.errors else None,
            n_resolved=len(self.evaluated),
            lead_ok_share=lead_ok_share(book),
            lead_hist=[S.LeadBucket(lead_min=m, count=leads.get(m, 0)) for m in LEAD_BUCKETS],
            alert_precision=round(hits / (hits + fa), 3) if hits + fa else None,
            alert_recall=round(hits / (hits + misses), 3) if hits + misses else None,
            offline=self.offline,
        )


def lead_ok_share(book: IncidentBook) -> float | None:
    """Доля алертов с упреждением ≥ 10 мин (цель — 100%)."""
    incs = list(book.incidents.values())
    if not incs:
        return None
    return round(sum(i.lead_min >= LEAD_OK_MIN for i in incs) / len(incs), 3)
