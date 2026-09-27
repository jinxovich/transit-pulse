"""Качество на потоке: алерты на ТС·час, медиана упреждения, MAE по упреждению, политика."""

from __future__ import annotations

import json
from collections import namedtuple
from datetime import datetime, timedelta

from services.backend.app.alerts.engine import AlertPolicy, IncidentBook
from services.backend.app.alerts.quality import QualityJournal, load_offline
from services.backend.app.ml_client import MlItem
from services.backend.app.pipeline.assemble import build_prediction
from services.backend.app.pipeline.prepare import VehicleTask, VisitTask
from transit_core import schemas as S

T = datetime(2026, 1, 6, 8, 0, 0)
Row = namedtuple("Row", "visit_id stop_key name lon lat tb")
OFFLINE = S.OfflineMetrics(cv_mae_baseline_s=90.0, cv_mae_model_s=60.0, improvement=0.333)


def _journal() -> QualityJournal:
    return QualityJournal(OFFLINE)


def _open(book: IncidentBook, vid: str, visit_id: int, lead: float) -> None:
    row = Row(visit_id, f"s{visit_id}", f"Остановка {visit_id}", 37.6, 55.7,
              T + timedelta(minutes=lead))  # fmt: skip
    visit = VisitTask(f"{vid}:{visit_id}", str(visit_id), 1, row, lead, {"lead": lead})
    task = VehicleTask(vid, 7, False, True, cur_dev=0.0, horizon_ok=True)
    item = MlItem(visit.item_id, 300.0, 240.0, 360.0, 0.9, 40.0)
    vp = build_prediction(task, visit, item, T, "ml", "test")
    book.open({"vehicle_id": vid, "tr_id": "7", "route_name": "7"}, vp, None, T)


def _minutes(j: QualityJournal) -> float:
    return j.vehicle_hours() * 60


def test_vehicle_minutes_accumulate_per_pass_interval():
    j = _journal()

    j.on_pass(T, ["a", "b"])  # первый проход — одна минута на ТС
    j.on_pass(T + timedelta(minutes=1), ["a", "b"])
    j.on_pass(T + timedelta(minutes=5), ["a"])  # пропущенные минуты тоже идут в зачёт

    assert _minutes(j) == 2 + 2 + 4


def test_pass_gap_is_capped():
    j = _journal()

    j.on_pass(T, ["a"])
    j.on_pass(T + timedelta(hours=2), ["a"])  # дыра в потоке — не часы работы

    assert _minutes(j) == 1 + QualityJournal.MAX_PASS_GAP_MIN


def test_repeated_pass_on_same_minute_adds_nothing():
    j = _journal()

    j.on_pass(T, ["a"])
    j.on_pass(T, ["a"])

    assert _minutes(j) == 1


def test_alerts_per_vehicle_hour_divides_incidents_by_vehicle_hours():
    j, book = _journal(), IncidentBook()
    for i in range(30):  # 30 минут × 2 ТС = 1 ТС·час
        j.on_pass(T + timedelta(minutes=i), ["a", "b"])
    _open(book, "a", 1, 12.0)
    _open(book, "b", 2, 13.0)

    q = j.contract(book)

    assert q.n_incidents == 2
    assert q.alerts_per_vehicle_hour == 2.0


def test_empty_session_has_no_rates():
    q = _journal().contract(IncidentBook())

    assert q.n_incidents == 0
    assert q.alerts_per_vehicle_hour is None
    assert q.lead_median_min is None
    assert [b.lead_min for b in q.mae_by_lead] == [11, 12, 13, 14, 15]
    assert all(b.mae_s is None and b.n == 0 for b in q.mae_by_lead)


def test_lead_median_over_incidents():
    book = IncidentBook()
    for vid, lead in (("a", 11.0), ("b", 14.0), ("c", 12.5)):
        _open(book, vid, 1, lead)

    assert _journal().contract(book).lead_median_min == 12.5


def test_mae_by_lead_buckets_by_minute_of_lead():
    j = _journal()
    j.record("a", "v1", 1, 100.0, 10.4)  # → 11
    j.record("a", "v1", 1, 160.0, 11.0)  # → 11 (минута (10, 11])
    j.record("b", "v2", 1, 50.0, 14.7)  # → 15
    j.record("c", "v3", 1, 10.0, 13.0)  # не сверен — не входит

    j.on_arrivals("a", {"v1": (T, 130.0)})
    j.on_arrivals("b", {"v2": (T, 20.0)})
    by = {b.lead_min: b for b in j.contract(IncidentBook()).mae_by_lead}

    assert (by[11].mae_s, by[11].n) == (30.0, 2)
    assert (by[15].mae_s, by[15].n) == (30.0, 1)
    assert by[13].mae_s is None and by[13].n == 0


def test_alert_policy_comes_from_book():
    book = IncidentBook(policy=AlertPolicy(delay_s=180.0, p_late=0.7, mode="and", min_streak=3))

    info = _journal().contract(book).alert_policy

    assert info == S.AlertPolicyInfo(delay_s=180.0, p_late=0.7, mode="and", min_streak=3)


def test_clear_drops_vehicle_minutes_and_lead_errors():
    j = _journal()
    j.on_pass(T, ["a"])
    j.record("a", "v1", 1, 100.0, 12.0)
    j.on_arrivals("a", {"v1": (T, 130.0)})

    j.clear()
    j.on_pass(T + timedelta(minutes=3), ["a"])

    assert _minutes(j) == 1
    assert all(b.n == 0 for b in j.contract(IncidentBook()).mae_by_lead)


def test_load_offline_reads_calibration(tmp_path):
    (tmp_path / "metrics.json").write_text(json.dumps({"stream": {
        "baseline_cur_dev": 114.1, "catboost_synthetic_mae": 77.8,
        "calibration": {"brier_before": 0.214, "brier_after": 0.171,
                        "coverage_q10_q90_scaled": 0.8009},
    }}))  # fmt: skip

    off = load_offline(tmp_path)

    assert (off.brier_before, off.brier_after) == (0.214, 0.171)
    assert off.interval_coverage == 0.801


def test_load_offline_coverage_falls_back_to_mean_by_lead(tmp_path):
    (tmp_path / "metrics.json").write_text(json.dumps({"stream": {
        "calibration": {"coverage_by_lead": {"11": 0.78, "13": 0.8, "15": 0.82}},
    }}))  # fmt: skip

    off = load_offline(tmp_path)

    assert off.interval_coverage == 0.8
    assert off.brier_before is None and off.brier_after is None


def test_load_offline_without_calibration_gives_none(tmp_path):
    (tmp_path / "metrics.json").write_text(json.dumps({"stream": {"baseline_cur_dev": 100.0}}))

    off = load_offline(tmp_path)

    assert (off.brier_before, off.brier_after, off.interval_coverage) == (None, None, None)
