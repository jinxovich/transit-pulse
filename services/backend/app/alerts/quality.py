"""Качество прогнозов на потоке: онлайн-MAE, упреждение алертов, precision/recall.

Журнал хранит «первый в окне» прогноз ТС на каждую сим-минуту (как карточка ТС).
Когда по GPS фиксируется прибытие на целевую остановку, прогнозы по ней сверяются
с фактом. ``miss`` — фактическое опоздание > порога red на цели, по рейсу которой
инцидента не было.

Нагрузка на диспетчера — инцидентов на ТС·час: часы копятся по проходам планировщика
(ТС с расписанием на связи и не в прогреве; каждый проход засчитывает сим-минуты с
предыдущего, пропущенные на высокой скорости минуты тоже).
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from transit_core import schemas as S

from .engine import IncidentBook
from .policy import AlertPolicy

log = logging.getLogger(__name__)
LEAD_BUCKETS = range(10, 16)
MAE_LEAD_BUCKETS = range(11, 16)
LEAD_OK_MIN = 10.0


def load_offline(models_dir: Path) -> S.OfflineMetrics:
    """Офлайн-метрики CV из ``models/metrics.json`` (если файла нет — нули)."""
    path = models_dir / "metrics.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return S.OfflineMetrics(cv_mae_baseline_s=0.0, cv_mae_model_s=0.0, improvement=0.0)
    data = data if isinstance(data, dict) else {}
    # Отчёт ML-сервиса: на потоке работает stream-модель — показываем её честное CV.
    src = data.get("stream") or data.get("offline") or data
    src = src if isinstance(src, dict) else {}
    base = _pick(
        src, "baseline_cur_dev", "cv_mae_baseline_s", "mae_baseline", "test_mae_baseline",
        "baseline_mae",
    )
    model = _pick(
        src, "catboost_synthetic_mae", "cv_mae_model_s", "mae_model", "test_mae_model",
        "model_mae",
    )
    impr = _pick(src, "improvement", "test_improvement")
    if impr == 0.0 and base > 0:
        impr = round(1 - model / base, 4)
    calib = src.get("calibration")
    calib = calib if isinstance(calib, dict) else {}
    return S.OfflineMetrics(
        cv_mae_baseline_s=base, cv_mae_model_s=model, improvement=impr,
        brier_before=_opt(calib.get("brier_before")), brier_after=_opt(calib.get("brier_after")),
        interval_coverage=_coverage(calib),
    )  # fmt: skip


def _pick(d: dict, *keys: str) -> float:
    for k in keys:
        if isinstance(d.get(k), int | float):
            return float(d[k])
    return 0.0


def _is_num(v: object) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _opt(v: object, digits: int = 4) -> float | None:
    return round(float(v), digits) if _is_num(v) else None


def _coverage(calib: dict) -> float | None:
    """Покрытие интервала [q10, q90]: откалиброванное, иначе среднее по упреждениям."""
    if (cov := _opt(calib.get("coverage_q10_q90_scaled"), 3)) is not None:
        return cov
    by_lead = calib.get("coverage_by_lead")
    vals = by_lead.values() if isinstance(by_lead, dict) else by_lead
    nums = [float(v) for v in vals if _is_num(v)] if isinstance(vals, Iterable) else []
    return round(statistics.fmean(nums), 3) if nums else None


class QualityJournal:
    """Журнал прогнозов сессии и их сверка с виртуальным фактом."""

    MAX_PASS_GAP_MIN = 15
    """Дольше между проходами — дыра в потоке, а не работа ТС: засчитываем не больше."""

    def __init__(self, offline: S.OfflineMetrics, th: S.Thresholds | None = None) -> None:
        self.offline = offline
        self.th = th or S.Thresholds()
        self.clear()

    def clear(self) -> None:
        self.pending: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
        self.trip_of: dict[tuple[str, str], int] = {}
        self.errors: list[float] = []
        self.errors_by_lead: dict[int, list[float]] = defaultdict(list)
        self.evaluated: set[tuple[str, str]] = set()
        self.late_targets: set[tuple[str, int]] = set()
        self.vehicle_min = 0.0
        self.last_pass: datetime | None = None

    def record(
        self, vehicle_id: str, visit_id: str, trip: int, predicted_s: float, lead_min: float
    ) -> None:
        """Запоминает прогноз по цели (только ``horizon_ok``)."""
        key = (vehicle_id, visit_id)
        if key not in self.evaluated:
            self.pending[key].append((predicted_s, lead_min))
            self.trip_of[key] = trip

    def on_arrivals(self, vehicle_id: str, arrivals: dict[str, tuple]) -> None:
        """Сверяет прогнозы с фактами прибытия ТС."""
        for visit_id, (_, actual) in arrivals.items():
            key = (vehicle_id, visit_id)
            preds = self.pending.pop(key, None)
            if preds is None:
                continue
            self.evaluated.add(key)
            for pred, lead in preds:
                err = abs(pred - actual)
                self.errors.append(err)
                self.errors_by_lead[lead_bucket(lead)].append(err)
            if actual > self.th.red_delay_s:
                self.late_targets.add((vehicle_id, self.trip_of[key]))

    def on_pass(self, t: datetime, vehicle_ids: Iterable[str]) -> None:
        """Проход планировщика на сим-минуте ``t``: ТС ``vehicle_ids`` работали с прошлого.

        Первый проход сессии засчитывает одну минуту, повторный на той же минуте — ноль.
        """
        if self.last_pass is None:
            minutes = 1.0
        else:
            gap = (t - self.last_pass).total_seconds() / 60
            minutes = min(max(gap, 0.0), float(self.MAX_PASS_GAP_MIN))
        self.last_pass = t if self.last_pass is None else max(self.last_pass, t)
        self.vehicle_min += minutes * len(set(vehicle_ids))

    def vehicle_hours(self) -> float:
        """ТС·часы сим-времени, накопленные проходами."""
        return self.vehicle_min / 60

    def mae_by_lead(self) -> list[S.LeadMae]:
        """Онлайн-MAE сверенных прогнозов по минуте упреждения 11…15."""
        out = []
        for m in MAE_LEAD_BUCKETS:
            errs = self.errors_by_lead.get(m, [])
            mae = round(statistics.fmean(errs), 1) if errs else None
            out.append(S.LeadMae(lead_min=m, mae_s=mae, n=len(errs)))
        return out

    def contract(self, book: IncidentBook) -> S.QualityMetrics:
        """Ответ ``GET /api/v1/metrics/quality``."""
        incs = list(book.incidents.values())
        outcomes = Counter(i.outcome for i in incs if i.status == "resolved")
        hits, fa = outcomes.get("hit", 0), outcomes.get("false_alarm", 0)
        misses = sum(1 for key in self.late_targets if key not in book.trips)
        leads = Counter(min(max(int(i.lead_min), LEAD_BUCKETS[0]), LEAD_BUCKETS[-1]) for i in incs)
        hours = self.vehicle_hours()
        return S.QualityMetrics(
            online_mae_s=round(sum(self.errors) / len(self.errors), 1) if self.errors else None,
            n_resolved=len(self.evaluated),
            lead_ok_share=lead_ok_share(book),
            lead_hist=[S.LeadBucket(lead_min=m, count=leads.get(m, 0)) for m in LEAD_BUCKETS],
            alert_precision=round(hits / (hits + fa), 3) if hits + fa else None,
            alert_recall=round(hits / (hits + misses), 3) if hits + misses else None,
            offline=self.offline,
            n_incidents=len(incs),
            alerts_per_vehicle_hour=round(len(incs) / hours, 2) if hours > 0 else None,
            lead_median_min=round(statistics.median(i.lead_min for i in incs), 1) if incs else None,
            mae_by_lead=self.mae_by_lead(),
            alert_policy=policy_info(book.policy),
        )


def lead_bucket(lead_min: float) -> int:
    """Минута упреждения ``(m − 1, m]`` → ``m``, прижатая к корзинам 11…15."""
    return min(max(math.ceil(lead_min), MAE_LEAD_BUCKETS[0]), MAE_LEAD_BUCKETS[-1])


def policy_info(policy: AlertPolicy) -> S.AlertPolicyInfo:
    """Политика алерта в виде контракта."""
    return S.AlertPolicyInfo(
        delay_s=policy.delay_s, p_late=policy.p_late, mode=policy.mode,
        min_streak=policy.min_streak,
    )  # fmt: skip


def lead_ok_share(book: IncidentBook) -> float | None:
    """Доля алертов с упреждением ≥ 10 мин (цель — 100%)."""
    incs = list(book.incidents.values())
    if not incs:
        return None
    return round(sum(i.lead_min >= LEAD_OK_MIN for i in incs) / len(incs), 3)
