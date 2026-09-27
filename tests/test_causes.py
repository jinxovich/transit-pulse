"""Причины задержки: правила и вклады признаков ML."""

import math

from transit_core.catalog import CAUSES, FEATURE_LABELS
from transit_core.causes import HIDDEN_EVIDENCE, format_value, infer_cause


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
    contrib = [{"feature": "gps_dev", "contribution_s": 80.0},
               {"feature": "cur_dev", "contribution_s": -5.0}]  # fmt: skip
    cause = infer_cause({"gps_dev": 150.0, "cur_dev": 10.0}, contrib, predicted_delay_s=130)
    assert cause.code == "ACCUMULATED_DELAY"
    assert [e.feature for e in cause.evidence] == ["gps_dev", "cur_dev"]
    assert cause.evidence[0].contribution_s == 80.0 and cause.evidence[0].value == "+2 мин 30 с"


def test_raw_eta_dev_hidden_but_still_gives_cause():
    contrib = [{"feature": "eta_dev", "contribution_s": 80.0},
               {"feature": "cur_dev", "contribution_s": -5.0}]  # fmt: skip
    f = {"eta_dev": 1440.0, "cur_dev": 10.0, "seg_speed": 12.0, "speed_ratio": 0.8}

    cause = infer_cause(f, contrib, predicted_delay_s=150)

    assert cause.code == "CONGESTION"
    assert [e.feature for e in cause.evidence] == ["cur_dev", "seg_speed", "speed_ratio"]


def test_delay_labels_say_where_it_is_measured():
    assert FEATURE_LABELS["cur_dev"] == "Отставание на последней пройденной остановке"
    assert FEATURE_LABELS["gps_dev"] == "Отставание от графика по GPS сейчас"


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


def test_planned_layover_is_not_long_dwell():
    f = {"cur_dev": 30.0, "spd5": 0.0, "stop5": 1.0, "dwell": 2222.0, "in_layover": 1.0}

    cause = infer_cause(f, predicted_delay_s=150)

    assert cause.code != "LONG_DWELL"
    shown = {e.feature: e.value for e in cause.evidence}
    assert shown["in_layover"] == "да"


def test_planned_layover_is_not_long_dwell_by_contributions():
    f = {"cur_dev": 10.0, "stop5": 1.0, "dwell": 1800.0, "in_layover": 1.0}
    contributions = [{"feature": "stop5", "contribution_s": 40.0}]

    assert infer_cause(f, contributions, predicted_delay_s=150).code != "LONG_DWELL"


def test_service_features_hidden_from_evidence_but_cause_unchanged():
    contrib = [{"feature": "hour_cos", "contribution_s": 90.0},
               {"feature": "lead", "contribution_s": 70.0},
               {"feature": "n_between", "contribution_s": 50.0},
               {"feature": "eta_dev", "contribution_s": 40.0},
               {"feature": "unlabeled_x", "contribution_s": 30.0},
               {"feature": "cur_dev", "contribution_s": 20.0}]  # fmt: skip
    f = {"eta_dev": 150.0, "cur_dev": 10.0, "hour_cos": 0.3, "lead": 12.0, "n_between": 4.0}

    cause = infer_cause(f, contrib, predicted_delay_s=130)

    assert cause.code == "CONGESTION"
    assert [e.feature for e in cause.evidence] == ["cur_dev"]


def test_contribution_without_value_does_not_give_accumulated_delay():
    contrib = [{"feature": "cur_dev", "contribution_s": 79.6},
               {"feature": "n_between", "contribution_s": 33.0},
               {"feature": "invalid15", "contribution_s": 16.8}]  # fmt: skip
    f = {"cur_dev": math.nan, "n_between": 9.0, "invalid15": 0.0}

    cause = infer_cause(f, contrib, predicted_delay_s=130)

    assert cause.code == "UNKNOWN"
    assert CAUSES["UNKNOWN"][0] == "Причина не определена"
    assert [e.feature for e in cause.evidence] == ["invalid15"]


def test_contribution_cause_falls_to_next_confirmed_feature():
    contrib = [{"feature": "cur_dev", "contribution_s": 90.0},
               {"feature": "gps_dev", "contribution_s": 40.0},
               {"feature": "spd15", "contribution_s": 20.0}]  # fmt: skip
    ahead = {"cur_dev": -30.0, "gps_dev": -10.0, "spd15": 12.0}

    assert infer_cause(ahead, contrib, predicted_delay_s=130).code == "CONGESTION"
    assert infer_cause({**ahead, "gps_dev": 50.0}, contrib, 130).code == "ACCUMULATED_DELAY"
    assert infer_cause({"cur_dev": 20.0}, contrib, 130).code == "ACCUMULATED_DELAY"


def test_speed_contribution_needs_known_value_and_no_layover():
    contrib = [{"feature": "spd15", "contribution_s": 50.0}]

    assert infer_cause({}, contrib, predicted_delay_s=130).code == "UNKNOWN"
    assert infer_cause({"spd15": 3.0}, contrib, predicted_delay_s=130).code == "CONGESTION"
    on_layover = {"spd15": 0.0, "dwell": 900.0, "in_layover": 1.0}
    assert infer_cause(on_layover, contrib, predicted_delay_s=130).code == "UNKNOWN"


def test_unknown_values_shown_only_when_nothing_else():
    contrib = [{"feature": "gps_dev", "contribution_s": 60.0},
               {"feature": "spd5", "contribution_s": 10.0}]  # fmt: skip

    only_unknown = infer_cause({}, contrib, predicted_delay_s=130)
    assert [e.value for e in only_unknown.evidence] == ["нет данных", "нет данных"]
    mixed = infer_cause({"spd5": 20.0}, contrib, predicted_delay_s=130)
    assert [e.feature for e in mixed.evidence] == ["spd5"]


def test_hidden_evidence_features_are_labeled_model_features():
    service = {"hour_sin", "hour_cos", "n_between", "eta_dev"}
    assert service <= HIDDEN_EVIDENCE <= set(FEATURE_LABELS)
