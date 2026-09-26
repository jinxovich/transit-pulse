"""Честная MAE stream-модели на потоке: фолд-модели, не видевшие точку, на потоковых признаках.

Финальная ``catboost_stream.cbm`` обучена на train+test, поэтому её ошибка на
``labels_test`` занижена. Здесь для каждой точки ``labels_test`` берётся модель фолда,
в обучение которого эта точка не попала (та же схема, что в CV обучения: GroupKFold по
``(tr_id, 30-мин блок)``, первый повтор, клоны рядом с валидационными точками выброшены),
и применяется к признакам, которые backend собрал на потоке в момент ``T``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.stream_eval_metrics import prf
from services.ml.app import cv
from services.ml.app.dataset import clone_sources, labeled, with_cur_dev
from services.ml.app.inference import to_matrix
from services.ml.app.models import fit_catboost, sort_quantiles
from transit_core.features import FEATURES
from transit_core.labels import LATE_S


def _stream_rows(sub: pd.DataFrame, stream_features: dict) -> list[dict]:
    return [stream_features[(int(r.tr_id), r.T.to_pydatetime(), int(r.target_stop_id))]
            for r in sub.itertuples()]  # fmt: skip


def honest_stream_preds(m: pd.DataFrame, stream_features: dict, models_dir: Path) -> pd.Series:
    """OOF-прогноз stream-модели для найденных на потоке точек (индекс — ``sample_id``)."""
    params = json.loads((models_dir / "metrics.json").read_text("utf-8"))["stream"]
    table = labeled()
    x, base = with_cur_dev(table.x, table.meta, "stream")
    y = table.meta["target_delay_s"].to_numpy(float) - base
    real = cv.real_positions(table.meta)
    folds = cv.make_folds(table.meta)[0]
    real_ids = table.meta["sample_id"].to_numpy()[real]
    want = m[m["found"] & (m["mode"] == "ml")].set_index("sample_id")
    sources, out = clone_sources(), {}
    for f in range(cv.N_SPLITS):
        val = real[folds == f]
        ids = [s for s in real_ids[folds == f] if s in want.index]
        if not ids:
            continue
        syn = cv.allowed_synthetic(table.meta, val, sources, honest=True)
        tr = np.concatenate([real[folds != f], syn]).astype(int)
        w = np.where(table.meta["synthetic"].to_numpy()[tr], params["best_synthetic_weight"], 1.0)
        model = fit_catboost(x.iloc[tr][FEATURES], y[tr], w, iterations=params["final_iterations"])
        sub = want.loc[ids].reset_index()
        xs = to_matrix(_stream_rows(sub, stream_features), FEATURES)
        q = sort_quantiles(model.predict(xs))
        pred = np.nan_to_num(xs[:, FEATURES.index("cur_dev")], nan=0.0) + q[:, 1]
        out.update(zip(ids, pred, strict=True))
    return pd.Series(out, name="honest_pred", dtype=float)


def honest_accuracy(m: pd.DataFrame, stream_features: dict, models_dir: Path) -> dict:
    """MAE честной stream-модели на потоке и baseline'ы на тех же точках."""
    pred = honest_stream_preds(m, stream_features, models_dir)
    f = m.set_index("sample_id").loc[pred.index]
    y = f["target_delay_s"].to_numpy(float)
    late = f["target_class"] == "late"
    return {
        "scheme": "GroupKFold по (tr_id, 30-мин блок), повтор 0, клоны рядом с валидацией "
        "выброшены; признаки — снятые на потоке",
        "n": len(pred),
        "mae_stream_model_honest": _mae(pred, y),
        "mae_stream_model_final_in_sample": _mae(f["stream_pred"], y),
        "mae_baseline_cur_dev_s": _mae(f["cur_dev_s"].fillna(0.0), y),
        "mae_baseline_online_cur_dev": _mae(f["stream_cur_dev"].fillna(0.0), y),
        "points_pred_gt_120_vs_late_honest": prf(pd.Series(pred.to_numpy() > LATE_S,
                                                           index=f.index), late),
        "points_pred_gt_120_vs_late_final": prf(f["stream_pred"] > LATE_S, late),
    }  # fmt: skip


def _mae(pred, y: np.ndarray) -> float:
    return round(float(np.abs(np.asarray(pred, float) - y).mean()), 2)
