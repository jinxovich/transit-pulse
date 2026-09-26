"""Причины задержки: правила и вклады признаков ML."""

import math

from transit_core.catalog import CAUSES
from transit_core.causes import format_value, infer_cause


def test_gps_loss_wins_when_no_data():
    assert infer_cause({"cur_dev": 300.0}, predicted_delay_s=300, stale=True).code == "GPS_LOSS"


def test_early_running():
    cause = infer_cause({"cur_dev": -120.0}, predicted_delay_s=-90)
    assert cause.code == "EARLY_RUNNING" and cause.title == CAUSES["EARLY_RUNNING"][0]


def test_short_layover_only_with_terminal_before_target():
    f = {"cur_dev": 400.0, "max_gap_between": 4.0, "trip_break_between": 1.0}
    assert infer_cause(f, predicted_delay_s=300).code == "SHORT_LAYOVER"
    f["trip_break_between"] = 0.0
    assert infer_cause(f, predicted_delay_s=300).code != "SHORT_LAYOVER"


def test_congestion_and_long_dwell():
    slow = {"cur_dev": 90.0, "spd5": 4.0, "stop5": 0.2, "gps_dev_trend": 40.0}
    assert infer_cause(slow, predicted_delay_s=200).code == "CONGESTION"
    dwell = {"cur_dev": 30.0, "spd5": 1.0, "stop5": 0.9, "dwell": 240.0}
    assert infer_cause(dwell, predicted_delay_s=150).code == "LONG_DWELL"


def test_accumulated_delay_and_nan_tolerance():
    f = {"cur_dev": 200.0, "spd5": math.nan, "gps_dev_trend": 10.0}
    cause = infer_cause(f, predicted_delay_s=230)
    assert cause.code == "ACCUMULATED_DELAY"
    assert all(e.contribution_s is None for e in cause.evidence)


def test_contributions_pick_cause_and_evidence():
    contrib = [{"feature": "eta_dev", "contribution_s": 80.0},
               {"feature": "cur_dev", "contribution_s": -5.0}]  # fmt: skip
    cause = infer_cause({"eta_dev": 150.0, "cur_dev": 10.0}, contrib, predicted_delay_s=130)
    assert cause.code == "CONGESTION"
    assert [e.feature for e in cause.evidence] == ["eta_dev", "cur_dev"]
    assert cause.evidence[0].contribution_s == 80.0 and cause.evidence[0].value == "+2 мин 30 с"


def test_unknown_when_nothing_matches():
    assert infer_cause({}, predicted_delay_s=130).code == "UNKNOWN"


def test_format_value_units():
    assert format_value("spd5", 4.25) == "4.2 км/ч"
    assert format_value("dist_tgt", 512.4) == "512 м"
    assert format_value("stop5", 0.5) == "50%"
    assert format_value("cur_dev", None) == "нет данных"


def test_format_value_for_schedule_and_quality_features():
    from transit_core.causes import format_value

    assert format_value("n_between", 3.0) == "3"
    assert format_value("trip_break_between", 1.0) == "да"
    assert format_value("tgt_manual", 0.0) == "нет"
    assert format_value("invalid15", 0.07) == "7%"
    assert format_value("trip_progress", 0.5) == "50%"
    assert format_value("tgt_gap", 12.0) == "12 мин"
