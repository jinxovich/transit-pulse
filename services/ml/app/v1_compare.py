"""LightGBM v1 (``scripts/quick_v1.py``: его признаки и параметры) на тех же фолдах, что v2."""

from __future__ import annotations

import pandas as pd

from scripts import quick_v1 as v1
from services.ml.app import cv
from services.ml.app.dataset import CACHE, RAW, SPLITS, Table
from services.ml.app.models import fit_predict_lgbm

V1_WEIGHT = 0.5


def v1_features() -> pd.DataFrame:
    """Признаки v1 для train+test в порядке :func:`services.ml.app.dataset.labeled`."""
    path = CACHE / "v1_features.pkl"
    if path.exists():
        return pd.read_pickle(path)
    parts = []
    for split in ("train", "test"):
        plan_f, traffic_f, points_f = SPLITS[split]
        pts = pd.read_csv(RAW / points_f)
        parts.append(v1.build_features(pts, v1.load_plan(RAW / plan_f),
                                       v1.load_traffic(RAW / traffic_f)))
    x = pd.concat(parts, ignore_index=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    x.to_pickle(path)
    return x


def v1_cv(table: Table, sources: dict[int, int]) -> dict:
    """MAE v1 на CV: с синтетикой (честно и наивно) и только реальные ТС."""
    x = v1_features()
    base = table.meta["cur_dev_s"].to_numpy(dtype=float)
    y = table.meta["target_delay_s"].to_numpy(dtype=float)
    real = cv.real_positions(table.meta)
    out = {}
    for name, w, honest in (("synthetic_0.5", V1_WEIGHT, True),
                            ("synthetic_0.5_naive", V1_WEIGHT, False),
                            ("real_only", 0.0, True)):
        oof = cv.run_cv(fit_predict_lgbm, x, y - base, table.meta, w, sources, honest)
        out[name] = cv.oof_mae(oof, base[real], y[real])
    return {k: round(float(v), 2) for k, v in out.items()}
