"""Сабмит v2: взвешенный ансамбль по OOF (``models/metrics.json`` → ``ensemble.weights``).

Компоненты: CatBoost (``models/catboost_submission.cbm``), LightGBM на наших признаках
(обучается здесь на train+test), GRU (``models/pred_validate_gru.csv`` из
``scripts/train_gru.py``), LightGBM v1 (``submissions/sub_v1_lgbm.csv``).
Берутся только компоненты с ненулевым весом.

Для validate используются только ``validate/schedule_plan.csv``, ``validate/traffic.csv``
(``event_time <= T`` — фильтр внутри ``point_features``) и ``cur_dev_s`` из ``points.csv``.

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


def _external(path: Path, col: str, sample_ids: pd.Series) -> np.ndarray:
    """Прогноз другой модели на validate из CSV, в порядке ``sample_ids``."""
    sep = ";" if path.suffix == ".csv" and ";" in path.open(encoding="utf-8").readline() else ","
    df = pd.read_csv(path, sep=sep).set_index("sample_id")
    return df.loc[sample_ids, col].to_numpy(dtype=float)


def _lgbm_residual(features: list[str], weight: float, x_val: pd.DataFrame) -> np.ndarray:
    """LightGBM на тех же признаках, обученный на всех размеченных точках."""
    table = labeled()
    x, base = with_cur_dev(table.x, table.meta, "submission")
    y = table.meta["target_delay_s"].to_numpy(dtype=float) - base
    w = np.where(table.meta["synthetic"].to_numpy(), weight, 1.0)
    return fit_predict_lgbm(x[features], y, w, x_val[features])


def component_predictions(weights: dict, metrics: dict, features: list[str]) -> dict:
    """Абсолютные прогнозы validate для компонентов с ненулевым весом."""
    val = load_split("validate")
    x, base = with_cur_dev(val.x, val.meta, "submission")
    sid = val.meta["sample_id"]
    out = {}
    if weights.get("catboost", 0) > 0:
        model = CatBoostRegressor()
        model.load_model(str(MODELS / "catboost_submission.cbm"))
        out["catboost"] = base + sort_quantiles(model.predict(x[features]))[:, 1]
    if weights.get("lgbm", 0) > 0:
        w = metrics["submission"]["best_synthetic_weight"]
        out["lgbm"] = base + _lgbm_residual(features, w, x)
    if weights.get("gru", 0) > 0:
        out["gru"] = _external(MODELS / "pred_validate_gru.csv", "pred", sid)
    if weights.get("lgbm_v1", 0) > 0:
        out["lgbm_v1"] = _external(ROOT / "submissions" / "sub_v1_lgbm.csv", "prediction", sid)
    return out


def main() -> int:
    metrics = json.loads((MODELS / "metrics.json").read_text("utf-8"))
    features = json.loads((MODELS / "feature_list.json").read_text("utf-8"))
    weights = {k: v for k, v in metrics["ensemble"]["weights"].items() if v > 0}
    preds = component_predictions(weights, metrics, features)
    pred = sum(weights[k] * preds[k] for k in weights) / sum(weights.values())
    sid = load_split("validate").meta["sample_id"]
    out = pd.DataFrame({"sample_id": sid, "prediction": np.round(pred, 2)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, sep=";", index=False)
    errors = validate_submission(OUT)
    print(f"{OUT.name}: {len(out)} строк, веса {weights}, среднее {pred.mean():.1f} c, "
          f"ошибки формата: {errors or 'нет'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
