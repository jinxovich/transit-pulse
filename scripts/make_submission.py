"""Сабмит v2: CatBoost (submission-модель) на validate, опционально бленд с LightGBM.

Для validate используются только ``validate/schedule_plan.csv``, ``validate/traffic.csv``
(``event_time <= T`` — фильтр внутри ``point_features``) и ``cur_dev_s`` из ``points.csv``.

Бленд включается, только если по OOF он лучше CatBoost (``models/metrics.json``).

Запуск::

    uv run python -m scripts.make_submission
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from scripts.validate_submission import validate_submission
from services.ml.app.dataset import labeled, load_split, with_cur_dev
from services.ml.app.models import fit_predict_lgbm, sort_quantiles

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
OUT = ROOT / "submissions" / "sub_v2_catboost.csv"
MIN_BLEND_GAIN_S = 0.3


def _blend_weight(metrics: dict) -> float:
    """Вес CatBoost в бленде; 1.0, если бленд не даёт выигрыша по OOF."""
    sub = metrics["submission"]
    gain = sub["catboost_synthetic_mae"] - sub["blend"]["mae"]
    return sub["blend"]["weight_catboost"] if gain >= MIN_BLEND_GAIN_S else 1.0


def _lgbm_residual(features: list[str], weight: float, x_val: pd.DataFrame) -> np.ndarray:
    """LightGBM на тех же признаках, обученный на всех размеченных точках."""
    table = labeled()
    x, base = with_cur_dev(table.x, table.meta, "submission")
    y = table.meta["target_delay_s"].to_numpy(dtype=float) - base
    w = np.where(table.meta["synthetic"].to_numpy(), weight, 1.0)
    return fit_predict_lgbm(x[features], y, w, x_val[features])


def main() -> int:
    metrics = json.loads((MODELS / "metrics.json").read_text("utf-8"))
    features = json.loads((MODELS / "feature_list.json").read_text("utf-8"))
    val = load_split("validate")
    x, base = with_cur_dev(val.x, val.meta, "submission")
    model = CatBoostRegressor()
    model.load_model(str(MODELS / "catboost_submission.cbm"))
    res = sort_quantiles(model.predict(x[features]))[:, 1]
    alpha = _blend_weight(metrics)
    if alpha < 1.0:
        lgb_res = _lgbm_residual(features, metrics["submission"]["best_synthetic_weight"], x)
        res = alpha * res + (1 - alpha) * lgb_res
    pred = base + res
    out = pd.DataFrame({"sample_id": val.meta["sample_id"], "prediction": np.round(pred, 2)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, sep=";", index=False)
    errors = validate_submission(OUT)
    print(f"{OUT.name}: {len(out)} строк, вес CatBoost {alpha:.1f}, "
          f"среднее {pred.mean():.1f} c, ошибки формата: {errors or 'нет'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
