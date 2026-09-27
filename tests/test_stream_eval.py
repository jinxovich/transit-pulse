"""stream_eval: малый прогон (1 ТС, 40 сим-мин) через NDTP → backend → ML и сверка с метками."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pandas as pd
import pytest
from backend_kit import RAW, has_raw

from scripts import stream_eval_metrics as M

TR_ID = 131672
START = datetime(2026, 1, 6, 1, 55)
END = datetime(2026, 1, 6, 2, 35)  # 40 сим-минут, первая точка разметки ТС — 02:35
MODELS = RAW.parents[1] / "models"


def _ready() -> bool:
    return has_raw() and (MODELS / "catboost_stream.cbm").is_file()


@pytest.fixture(scope="module")
def small_run():
    from scripts.stream_eval import report
    from scripts.stream_eval_run import RunConfig, run_stream

    cfg = RunConfig(START, END, tr_ids=frozenset({TR_ID}), typical_speeds=False)
    labels = M.load_labels(cfg.data_dir, START, END, cfg.tr_ids)
    log = asyncio.run(run_stream(cfg, M.label_keys(labels)))
    rep, matched = report(cfg, labels, log, honest=False)
    return log, rep, matched


@pytest.mark.skipif(not _ready(), reason="нет data/raw или моделей")
def test_stream_predictions_respect_horizon_and_are_never_retroactive(small_run):
    log, rep, _ = small_run
    preds = pd.DataFrame(log.preds)
    assert len(preds) > 0 and log.ingest["dropped"] == 0 and log.ingest["crc_errors"] == 0
    ok = preds[preds["horizon_ok"]]
    assert ((ok["lead_min"] > 10) & (ok["lead_min"] <= 15)).all()
    assert (preds.loc[~preds["horizon_ok"], "lead_min"] > 15).all()
    gen, planned = pd.to_datetime(preds["generated_at"]), pd.to_datetime(preds["planned_at"])
    assert (gen < planned).all() and (gen == preds["T"]).all()
    assert rep["horizon"]["horizon_ok_lead_in_window_share"] == 1.0
    assert rep["horizon"]["generated_before_planned_share"] == 1.0


@pytest.mark.skipif(not _ready(), reason="нет data/raw или моделей")
def test_stream_forecast_matches_label_target_and_offline_model(small_run):
    _, rep, matched = small_run
    assert len(matched) >= 1
    assert matched["found"].all(), matched[["T", "miss_reason"]]
    assert (matched["mode"] == "ml").all()
    assert rep["coverage"]["share"] == 1.0
    # паритет: та же модель на признаках из CSV даёт тот же прогноз (до округления JSON)
    assert rep["parity"]["max_abs_diff_s"] <= 0.1
    assert rep["parity"]["features_mismatch"] == {}
    assert rep["accuracy"]["mae_stream_model"] is not None


def test_match_explains_missing_forecasts():
    t = pd.Timestamp("2026-01-06 08:00")
    labels = pd.DataFrame({
        "sample_id": ["a", "b", "c", "d"], "tr_id": [1, 2, 3, 4], "T": [t] * 4,
        "target_stop_id": [10, 20, 30, 40], "cur_dev_s": [0.0] * 4,
    })  # fmt: skip
    preds = pd.DataFrame([{
        "tr_id": 1, "T": t, "visit_id": 10, "delay_s": 5.0, "p_late": 0.1, "risk": "green",
        "mode": "ml", "lead_min": 11.0, "horizon_ok": True, "cur_dev": 3.0, "stale": False,
        "first": True, "generated_at": "2026-01-06T08:00:00", "planned_at": "2026-01-06T08:11:00",
    }])  # fmt: skip
    minutes = pd.DataFrame({"tr_id": [1, 2, 3], "T": [t] * 3, "ready": [True, False, True],
                            "n_visits": [1, 0, 2]})  # fmt: skip
    m = M.match(labels, preds, minutes)
    assert m["found"].tolist() == [True, False, False, False]
    assert m["miss_reason"].tolist()[1:] == [M.MISS_WARMUP, M.MISS_NOT_IN_WINDOW,
                                            M.MISS_NO_VEHICLE]  # fmt: skip


@pytest.mark.skipif(not _ready(), reason="нет data/raw или моделей")
def test_every_window_prediction_keeps_its_stream_features(small_run):
    log, _, _ = small_run
    keys = {(p["tr_id"], p["T"], p["visit_id"]) for p in log.preds}

    assert keys and keys <= set(log.features)


def test_honest_fold_is_taken_from_nearest_label_of_same_vehicle():
    import numpy as np

    from scripts.stream_dump import DEFAULT_FOLD, assign_folds
    from scripts.stream_eval_honest import FoldModels

    t0 = pd.Timestamp("2026-01-06 08:00")
    meta = pd.DataFrame(
        {"sample_id": ["a", "b"], "tr_id": [1, 1], "T": [t0, t0 + pd.Timedelta("2h")]}
    )
    fm = FoldModels(models=[], meta=meta, fold_of=pd.Series([3, 4], index=["a", "b"]))
    preds = pd.DataFrame({
        "tr_id": [1, 1, 1, 2],
        "T": [t0 + pd.Timedelta("10min"), t0 + pd.Timedelta("110min"), t0 + pd.Timedelta("1h"), t0],
    })  # fmt: skip

    folds = assign_folds(preds, fm)

    np.testing.assert_array_equal(folds, [3, 4, DEFAULT_FOLD, DEFAULT_FOLD])


def test_recalibrate_scales_interval_by_lead_and_maps_p_late():
    import numpy as np

    from scripts.stream_dump import recalibrate
    from services.ml.app.inference import Calibration

    cal = Calibration.from_dict({
        "version": 1, "global_scale": 1.0, "err_k": 0.5,
        "lead_bins": [{"lo": 10, "hi": 12, "scale": 2.0}, {"lo": 12, "hi": 15, "scale": 3.0}],
        "isotonic": {"x": [0.0, 1.0], "y": [0.0, 0.5]},
    })  # fmt: skip
    preds = pd.DataFrame({
        "h_base": [100.0, 100.0], "h_q10r": [-10.0, -10.0], "h_q50r": [0.0, 0.0],
        "h_q90r": [10.0, 10.0], "f_lead": [11.0, 14.0],
        "h_delay": [0.0, 0.0], "h_q10": [0.0, 0.0], "h_q90": [0.0, 0.0], "h_p_late": [0.0, 0.0],
    })  # fmt: skip

    out = recalibrate({"preds": preds, "interval_scale": 1.0}, cal)["preds"]

    np.testing.assert_allclose(out["h_q90"] - out["h_delay"], [20.0, 30.0])
    assert (out["h_p_late"] <= 0.5).all()
