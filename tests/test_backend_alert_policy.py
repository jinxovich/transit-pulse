"""Политика алертов: пороги, комбинатор or/and и серия проходов перед открытием инцидента."""

from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timedelta

import pytest

from services.backend.app.alerts.engine import AlertPolicy, IncidentBook
from services.backend.app.config import Settings
from services.backend.app.ml_client import MlItem
from services.backend.app.pipeline.assemble import VisitPrediction, build_prediction
from services.backend.app.pipeline.prepare import VehicleTask, VisitTask

T = datetime(2026, 1, 6, 8, 0, 0)
Row = namedtuple("Row", "visit_id stop_key name lon lat tb")
VID = "7"


def _vp(visit_id: int, delay: float, p_late: float, lead: float = 12.0,
        horizon_ok: bool = True) -> VisitPrediction:  # fmt: skip
    row = Row(visit_id, f"s{visit_id}", f"Остановка {visit_id}", 37.6, 55.7,
              T + timedelta(minutes=lead))  # fmt: skip
    visit = VisitTask(f"{VID}:{visit_id}", str(visit_id), 1, row, lead, {"lead": lead})
    task = VehicleTask(VID, 7, False, True, cur_dev=0.0, horizon_ok=horizon_ok)
    item = MlItem(visit.item_id, delay, delay - 60, delay + 60, p_late, 40.0)
    return build_prediction(task, visit, item, T, "ml", "test")


def _book(**kw) -> IncidentBook:
    return IncidentBook(policy=AlertPolicy(**kw))


def test_single_red_pass_does_not_open_with_min_streak_2():
    book, vp = _book(min_streak=2), _vp(70, 300.0, 0.9)

    book.observe(VID, [vp])

    assert not book.can_open(VID, vp, T)


def test_two_consecutive_passes_open():
    book, vp = _book(min_streak=2), _vp(70, 300.0, 0.9)

    book.observe(VID, [vp])
    book.observe(VID, [vp])

    assert book.can_open(VID, vp, T)


def test_streak_resets_when_condition_fails():
    book, red, calm = _book(min_streak=2), _vp(70, 300.0, 0.9), _vp(70, 10.0, 0.1)

    book.observe(VID, [red])
    book.observe(VID, [calm])
    book.observe(VID, [red])

    assert book.streak(VID, "70") == 1
    assert not book.can_open(VID, red, T)


def test_streak_resets_when_visit_leaves_window():
    book, red, other = _book(min_streak=2), _vp(70, 300.0, 0.9), _vp(71, 300.0, 0.9)

    book.observe(VID, [red])
    book.observe(VID, [other])
    book.observe(VID, [red])

    assert book.streak(VID, "70") == 1
    assert book.streak(VID, "71") == 0


def test_streak_counts_only_window_visits():
    book, out = _book(min_streak=1), _vp(70, 300.0, 0.9, lead=6.0, horizon_ok=False)

    book.observe(VID, [out])

    assert book.streak(VID, "70") == 0


def test_streaks_are_per_vehicle():
    book, vp = _book(min_streak=2), _vp(70, 300.0, 0.9)

    book.observe(VID, [vp])
    book.observe("8", [])
    book.observe(VID, [vp])

    assert book.streak(VID, "70") == 2


@pytest.mark.parametrize(
    ("delay", "p_late", "mode", "hit"),
    [
        (200.0, 0.1, "or", True),
        (10.0, 0.9, "or", True),
        (200.0, 0.1, "and", False),
        (10.0, 0.9, "and", False),
        (200.0, 0.9, "and", True),
        (150.0, 0.59, "or", False),  # порог по задержке строгий
    ],
)
def test_alert_condition_thresholds_and_mode(delay, p_late, mode, hit):
    policy = AlertPolicy(delay_s=150.0, p_late=0.6, mode=mode, min_streak=1)

    assert policy.hit(_vp(70, delay, p_late).prediction) is hit


def test_p_late_threshold_is_inclusive():
    policy = AlertPolicy(delay_s=150.0, p_late=0.6, mode="or", min_streak=1)
    assert policy.hit(_vp(70, 10.0, 0.6).prediction)


def test_alert_needs_policy_not_just_red_colour():
    """Красный на карте (delay > 120), но ниже порога алерта (150) — инцидента нет."""
    book, vp = _book(delay_s=150.0, p_late=0.6, min_streak=1), _vp(70, 130.0, 0.4)
    assert vp.prediction.risk_level == "red"

    book.observe(VID, [vp])

    assert not book.can_open(VID, vp, T)


def test_prior_conditions_still_apply():
    book, vp = _book(min_streak=1), _vp(70, 300.0, 0.9)
    book.observe(VID, [vp])
    assert book.can_open(VID, vp, T)
    assert not book.can_open(VID, vp, T + timedelta(minutes=13))  # цель уже позади

    book.open({"vehicle_id": VID, "tr_id": "7", "route_name": "r"}, vp, None, T)

    assert not book.can_open(VID, vp, T)  # у ТС уже есть активный инцидент


def test_clear_keeps_policy_and_drops_streaks():
    book, vp = _book(min_streak=3), _vp(70, 300.0, 0.9)
    book.observe(VID, [vp])

    book.clear()

    assert book.policy.min_streak == 3 and book.streak(VID, "70") == 0


@pytest.mark.parametrize(("kw", "msg"), [({"mode": "xor"}, "ALERT_MODE"),
                                          ({"min_streak": 0}, "ALERT_MIN_STREAK")])  # fmt: skip
def test_invalid_policy_fails_fast(kw, msg):
    with pytest.raises(ValueError, match=msg):
        AlertPolicy(**kw)


def test_default_policy_is_selected_one():
    p = AlertPolicy()
    assert (p.delay_s, p.p_late, p.mode, p.min_streak) == (150.0, 0.6, "or", 2)


def test_settings_read_alert_env(monkeypatch):
    monkeypatch.setenv("ALERT_DELAY_S", "180")
    monkeypatch.setenv("ALERT_P_LATE", "0.7")
    monkeypatch.setenv("ALERT_MODE", "AND")
    monkeypatch.setenv("ALERT_MIN_STREAK", "3")

    s = Settings.from_env()

    assert (s.alert_delay_s, s.alert_p_late, s.alert_mode, s.alert_min_streak) == (
        180.0, 0.7, "and", 3,
    )  # fmt: skip
    assert s.alert_policy() == AlertPolicy(180.0, 0.7, "and", 3)


def test_settings_alert_defaults(monkeypatch):
    for name in ("ALERT_DELAY_S", "ALERT_P_LATE", "ALERT_MODE", "ALERT_MIN_STREAK"):
        monkeypatch.delenv(name, raising=False)

    assert Settings.from_env().alert_policy() == AlertPolicy()


def test_runtime_book_uses_settings_policy():
    from backend_kit import has_raw, make_runtime

    if not has_raw():
        pytest.skip("нет data/raw")
    rt = make_runtime(alert_min_streak=3, alert_mode="and")

    assert rt.book.policy == AlertPolicy(mode="and", min_streak=3)
