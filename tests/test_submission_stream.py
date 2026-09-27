"""Сабмит из потока: прогноз берётся по ключу (ТС, T, целевая остановка)."""

import pandas as pd

from scripts.make_submission_stream import pick_predictions

POINTS = pd.DataFrame({
    "sample_id": ["a", "b", "c"],
    "tr_id": [1, 1, 2],
    "T": pd.to_datetime(["2026-01-06 07:00", "2026-01-06 07:05", "2026-01-06 07:00"]),
    "target_stop_id": [10, 11, 20],
})


def _preds(rows):
    return pd.DataFrame(rows, columns=["tr_id", "T", "visit_id", "delay_s", "mode"])


def test_picks_prediction_for_target_stop_at_moment_t():
    preds = _preds([
        (1, "2026-01-06T07:00:00", 10, 42.123, "ml"),
        (1, "2026-01-06T07:00:00", 11, 99.0, "ml"),  # другая остановка окна
        (1, "2026-01-06T07:05:00", 11, 7.0, "ml"),
        (2, "2026-01-06T07:00:00", 20, -30.0, "fallback"),
    ])

    out = pick_predictions(POINTS, preds)

    assert out["prediction"].tolist() == [42.12, 7.0, -30.0]
    assert out["mode"].tolist() == ["ml", "ml", "fallback"]


def test_missing_stream_prediction_is_nan():
    preds = _preds([(1, "2026-01-06T07:00:00", 10, 1.0, "ml")])

    out = pick_predictions(POINTS, preds)

    assert out["prediction"].isna().tolist() == [False, True, True]
