"""Честный сабмит: CatBoost без синтетических копий моментов validate.

Синтетические ТС в train — сдвинутые копии реальных, в том числе в блоках validate: их метки
почти повторяют настоящие задержки validate (корреляция 0.88–0.99). Модель, обученная на всём
train, частично «узнаёт» ответы, отсюда скор 1.0 на платформе. Здесь из обучения убраны
синтетические точки, чей реальный источник имеет точку validate в пределах ±45 мин (та же
защита, что в честном CV: ``cv.allowed_synthetic``). Реальные точки train и test остаются.

Скор этого сабмита показывает, как модель работает без «узнавания» — ориентир для нового дня.

Запуск::

    uv run python -m scripts.make_submission_honest
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.validate_submission import validate_submission
from services.ml.app import cv
from services.ml.app.dataset import clone_sources, labeled, load_split, with_cur_dev
from services.ml.app.models import fit_catboost, sort_quantiles
from transit_core.features import FEATURES

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
OUT = ROOT / "submissions" / "sub_v3_honest.csv"


def allowed_for_validate(meta: pd.DataFrame, val_meta: pd.DataFrame) -> np.ndarray:
    """Позиции синтетических точек ``meta``, не являющихся копиями моментов validate."""
    both = pd.concat(
        [meta[["tr_id", "T", "synthetic"]], val_meta[["tr_id", "T"]].assign(synthetic=False)],
        ignore_index=True,
    )
    val_pos = np.arange(len(meta), len(both))
    return cv.allowed_synthetic(both, val_pos, clone_sources(), honest=True)


def main() -> int:
    params = json.loads((MODELS / "metrics.json").read_text("utf-8"))["submission"]
    table, val = labeled(), load_split("validate")
    x, base = with_cur_dev(table.x, table.meta, "submission")
    y = table.meta["target_delay_s"].to_numpy(float) - base
    real = cv.real_positions(table.meta)
    syn = allowed_for_validate(table.meta, val.meta)
    n_syn = int(table.meta["synthetic"].sum())
    train = np.concatenate([real, syn]).astype(int)
    w = np.where(table.meta["synthetic"].to_numpy()[train], params["best_synthetic_weight"], 1.0)
    model = fit_catboost(x.iloc[train][FEATURES], y[train], w,
                         iterations=params["final_iterations"])  # fmt: skip
    xv, bv = with_cur_dev(val.x, val.meta, "submission")
    pred = bv + sort_quantiles(model.predict(xv[FEATURES]))[:, 1]
    out = pd.DataFrame({"sample_id": val.meta["sample_id"], "prediction": np.round(pred, 2)})
    out.to_csv(OUT, sep=";", index=False)
    problems = validate_submission(OUT)
    print(f"синтетики в обучении: {len(syn)} из {n_syn} (убраны копии моментов validate)")
    print(f"{OUT}: {'OK' if not problems else problems}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
