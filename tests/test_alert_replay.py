"""Офлайн-реплей политики алертов: синтетический дамп из пары ТС и сим-минут."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from scripts import alert_replay as R

T0 = datetime(2026, 1, 6, 8, 0)


def _row(tr: int, minute: int, visit: int, planned_min: int, delay: float, p: float) -> dict:
    t = T0 + timedelta(minutes=minute)
    planned = T0 + timedelta(minutes=planned_min)
    return {
        "tr_id": tr, "T": t, "visit_id": visit, "planned_at": planned.isoformat(),
        "lead_min": (planned - t).total_seconds() / 60, "horizon_ok": True, "stale": False,
        "delay_s": delay, "p_late": p, "h_delay": delay, "h_p_late": p,
    }  # fmt: skip


def _fact(rows: list[tuple[int, int, int, int, float]]) -> pd.DataFrame:
    """``(visit_id, tr_id, trip, planned_min, delay_s)`` → таблица факта."""
    out = []
    for visit, tr, trip, planned_min, delay in rows:
        tb = T0 + timedelta(minutes=planned_min)
        out.append({"visit_id": visit, "tr_id": tr, "trip": trip, "tb": tb,
                    "tf": tb + timedelta(seconds=delay), "delay_s": delay})  # fmt: skip
    return pd.DataFrame(out)


def test_streak_counts_consecutive_passes_and_resets():
    preds = pd.DataFrame([
        _row(1, 0, 10, 14, 200, 0.9),
        _row(1, 1, 10, 14, 200, 0.9),
        _row(1, 2, 10, 14, 50, 0.1),  # не удовлетворил — сброс
        _row(1, 3, 10, 14, 200, 0.9),
    ])  # fmt: skip
    ok = R.signal(preds, R.Policy())
    assert list(R.streaks(preds, ok)) == [1, 2, 0, 1]


def test_streak_breaks_on_missing_pass():
    preds = pd.DataFrame([_row(1, 0, 10, 14, 200, 0.9), _row(1, 2, 10, 14, 200, 0.9)])
    ok = R.signal(preds, R.Policy())
    assert list(R.streaks(preds, ok)) == [1, 1]


def test_signal_and_requires_both_conditions():
    preds = pd.DataFrame([_row(1, 0, 10, 14, 200, 0.2), _row(1, 0, 11, 15, 50, 0.9),
                          _row(1, 0, 12, 13, 200, 0.9)])  # fmt: skip
    assert list(R.signal(preds, R.Policy(mode="or"))) == [True, True, True]
    assert list(R.signal(preds, R.Policy(mode="and"))) == [False, False, True]


def test_signal_uses_selected_source():
    row = _row(1, 0, 10, 14, 50, 0.1) | {"h_delay": 300.0}
    preds = pd.DataFrame([row])
    assert list(R.signal(preds, R.Policy(source="honest"))) == [True]
    assert list(R.signal(preds, R.Policy(source="final"))) == [False]


def test_min_streak_delays_opening_and_one_incident_per_vehicle():
    preds = pd.DataFrame([
        _row(1, 0, 10, 14, 200, 0.9), _row(1, 0, 11, 15, 200, 0.9),
        _row(1, 1, 10, 14, 200, 0.9), _row(1, 1, 11, 15, 200, 0.9),
    ])  # fmt: skip
    fact = _fact([(10, 1, 1, 14, 200), (11, 1, 1, 15, 200)])

    one = R.simulate(preds, R.Policy(min_streak=1), fact)
    two = R.simulate(preds, R.Policy(min_streak=2), fact)

    assert list(one["visit_id"]) == [10] and list(one["created"]) == [T0]
    assert list(two["visit_id"]) == [10]
    assert list(two["created"]) == [T0 + timedelta(minutes=1)]


def test_next_incident_after_fact_arrival_and_no_duplicate_target():
    preds = pd.DataFrame([
        _row(1, 0, 10, 12, 200, 0.9),
        _row(1, 1, 11, 13, 200, 0.9),  # цель 10 ещё не пройдена — ТС занято
        _row(1, 3, 10, 14, 200, 0.9),  # та же цель — дубль, не открываем
        _row(1, 3, 11, 13, 200, 0.9),  # 10 пройдена в 08:02 → новый инцидент
    ])  # fmt: skip
    fact = _fact([(10, 1, 1, 0, 120), (11, 1, 1, 13, 30)])  # факт 10: 08:02

    inc = R.simulate(preds, R.Policy(), fact)

    assert list(inc["visit_id"]) == [10, 11]
    assert list(inc["created"]) == [T0, T0 + timedelta(minutes=3)]


def test_timeout_closes_incident_without_fact():
    preds = pd.DataFrame([_row(1, 0, 10, 12, 60, 0.9), _row(1, 33, 11, 45, 200, 0.9),
                          _row(1, 34, 12, 46, 200, 0.9)])  # fmt: skip
    fact = _fact([(11, 1, 1, 45, 300), (12, 1, 1, 46, 300)])  # у цели 10 нет факта

    inc = R.simulate(preds, R.Policy(), fact)

    # таймаут 10: 08:12 + 60 c + 20 мин = 08:33, закрывается строго после — в 08:34
    assert list(inc["visit_id"]) == [10, 12]


def test_evaluate_precision_recall_and_lead():
    preds = pd.DataFrame([
        _row(1, 0, 10, 14, 200, 0.9),  # алерт, факт 300 — hit
        _row(1, 0, 11, 15, 0, 0.0),
        _row(2, 0, 20, 12, 200, 0.9),  # алерт, факт 30 — ложный
        _row(2, 30, 21, 42, 0, 0.0),   # опоздал (факт 400), алерта нет — промах рейса 2
    ])  # fmt: skip
    fact = _fact([(10, 1, 1, 14, 300), (11, 1, 1, 15, 500), (20, 2, 1, 12, 30),
                  (21, 2, 2, 42, 400)])  # fmt: skip
    minutes = pd.DataFrame({"tr_id": [1] * 60 + [2] * 60, "T": [T0] * 120,
                            "ready": True, "stale": False})  # fmt: skip

    res = R.replay(preds, R.Policy(), fact, minutes)

    assert res["n_incidents"] == 2
    assert res["precision"] == pytest.approx(0.5)
    assert res["trip_recall"] == pytest.approx(0.5)  # поздние рейсы (1,1) и (2,2)
    assert res["visit_recall"] == pytest.approx(1 / 3)  # поздние 10, 11, 21
    assert res["alerts_per_vehicle_hour"] == pytest.approx(1.0)
    assert res["lead_ge_10_share"] == 1.0 and res["lead_median_min"] == 13.0
    assert res["f1"] == pytest.approx(0.5)


def test_select_prefers_f1_under_recall_floor_then_fewer_alerts():
    rows = [
        {"f1": 0.9, "trip_recall": 0.6, "lead_ge_10_share": 1.0, "alerts_per_vehicle_hour": 1},
        {"f1": 0.7, "trip_recall": 0.8, "lead_ge_10_share": 1.0, "alerts_per_vehicle_hour": 2},
        {"f1": 0.7, "trip_recall": 0.75, "lead_ge_10_share": 1.0, "alerts_per_vehicle_hour": 1},
        {"f1": 0.8, "trip_recall": 0.9, "lead_ge_10_share": 0.9, "alerts_per_vehicle_hour": 1},
    ]  # fmt: skip
    best, constrained = R.select(rows, min_recall=0.7)
    assert best is rows[2] and constrained
    best, constrained = R.select(rows, min_recall=0.95)
    assert best is rows[0] and not constrained
