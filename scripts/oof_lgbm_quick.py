"""OOF LightGBM на признаках ``quick_v1`` по той же CV, что и GRU: эталон для сравнения.

Фолды — ``seq_data.fold_ids`` (те же группы и перестановки), синтетика с весом 0.5 только
в обучающих фолдах, оценка только на реальных ТС.

Запуск (после ``scripts.train_gru``, нужен кэш ``data/cache/seq_labeled.csv``)::

    uv run python -m scripts.oof_lgbm_quick
"""

from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from scripts import quick_v1 as q
from services.ml.app.seq_data import N_FOLDS, fold_ids

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW = REPO_ROOT / "data" / "raw"
REPEATS = 3
SEEDS = 2
THREADS = 4


def features() -> pd.DataFrame:
    """Признаки quick_v1 для train+test в том же порядке, что и кэш GRU."""
    parts = []
    for split, labels in (("train", "labels_train.csv"), ("test", "labels_test.csv")):
        lb = pd.read_csv(RAW / "labels" / labels)
        plan = q.load_plan(RAW / split / "schedule.csv")
        parts.append(q.build_features(lb, plan, q.load_traffic(RAW / split / "traffic.csv")))
    return pd.concat(parts, ignore_index=True)


def main() -> int:
    pts = pd.read_csv(REPO_ROOT / "data" / "cache" / "seq_labeled.csv")
    x = features()
    real = pts["is_real"].to_numpy(bool)
    y, cur = pts["target_delay_s"].to_numpy(float), pts["cur_dev_s"].to_numpy(float)
    w = np.where(real, 1.0, q.SYNTHETIC_WEIGHT)
    rows = []
    for rep in range(REPEATS):
        folds = np.full(len(pts), -1)
        folds[real] = fold_ids(pts.loc[real, "group"], rep)
        for f in range(N_FOLDS):
            va = real & (folds == f)
            tr = ~va
            ds = lgb.Dataset(x[tr], y[tr] - cur[tr], weight=w[tr])
            params = {**q.PARAMS, "num_threads": THREADS}
            resid = np.mean([
                lgb.train({**params, "seed": s}, ds, q.NUM_ROUNDS).predict(x[va])
                for s in range(SEEDS)
            ], axis=0)
            rows.append(pd.DataFrame({
                "sample_id": pts.loc[va, "sample_id"].to_numpy(), "fold_repeat": rep,
                "oof_pred": cur[va] + resid, "y": y[va],
            }))
    oof = pd.concat(rows, ignore_index=True)
    oof.round(3).to_csv(REPO_ROOT / "models" / "oof_lgbm_quick.csv", index=False)
    mae = (oof["y"] - oof["oof_pred"]).abs().groupby(oof["fold_repeat"]).mean()
    print(f"LightGBM quick_v1 OOF-MAE: {mae.round(2).tolist()}, среднее {mae.mean():.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
