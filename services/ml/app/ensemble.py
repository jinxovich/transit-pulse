"""Ансамбль для сабмита: веса CatBoost / LightGBM / GRU / LightGBM-v1 по OOF на общих фолдах.

Фолды — схема GRU-ветки (``cv.make_folds(scheme="seq")``), чтобы OOF всех моделей
стыковались по ``(sample_id, fold_repeat)``. Режим — «наивный» (синтетика без clone guard),
как у OOF GRU: для лидерборда он и релевантен (клоны validate-блоков реально лежат в train,
организаторы разрешают на них учиться). Честные цифры (clone guard) остаются в
``metrics.json`` → ``submission``.

Запуск (после ``services.ml.app.train``)::

    uv run python -m services.ml.app.ensemble
"""

from __future__ import annotations

import itertools
import json
import logging
import sys

import numpy as np
import pandas as pd

from services.ml.app import cv
from services.ml.app.dataset import clone_sources, labeled, with_cur_dev
from services.ml.app.train import MODELS, _lgb_fit_predict, catboost_cv

STEP = 0.05
EXTERNAL = {"gru": "oof_gru.csv", "lgbm_v1": "oof_lgbm_quick.csv"}
log = logging.getLogger("ensemble")


def own_oof(table, sources, weight: float) -> tuple[pd.DataFrame, dict]:
    """OOF CatBoost и LightGBM (наши признаки) на фолдах GRU, наивный режим."""
    x, base = with_cur_dev(table.x, table.meta, "submission")
    y = table.meta["target_delay_s"].to_numpy(dtype=float)
    real = cv.real_positions(table.meta)
    cb = catboost_cv(x, base, y, table.meta, sources, weight, honest=False, scheme="seq")
    lgb = cv.run_cv(_lgb_fit_predict, x, y - base, table.meta, weight, sources, honest=False,
                    scheme="seq")
    b, sid = base[real], table.meta["sample_id"].to_numpy()[real]
    rows = [pd.DataFrame({"sample_id": sid, "fold_repeat": r, "catboost": b + cb["oof"][r, :, 1],
                          "catboost_q10": b + cb["oof"][r, :, 0],
                          "catboost_q90": b + cb["oof"][r, :, 2],
                          "lgbm": b + lgb[r], "y": y[real]})
            for r in range(cv.REPEATS)]
    return pd.concat(rows, ignore_index=True), {"catboost_iters": cb["iters_common"]}


def attach_external(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет OOF других веток по ``(sample_id, fold_repeat)``, если файлы есть."""
    for name, file in EXTERNAL.items():
        path = MODELS / file
        if not path.exists():
            continue
        ext = pd.read_csv(path)[["sample_id", "fold_repeat", "oof_pred"]]
        df = df.merge(ext.rename(columns={"oof_pred": name}), on=["sample_id", "fold_repeat"],
                      how="left")
    return df


def best_weights(df: pd.DataFrame, names: list[str]) -> tuple[dict, float]:
    """Перебор выпуклых весов с шагом :data:`STEP`; MAE усреднён по повторам."""
    grid = np.round(np.arange(0, 1 + STEP / 2, STEP), 2)
    preds, y = df[names].to_numpy(), df["y"].to_numpy()
    best_w, best_mae = None, np.inf
    for combo in itertools.product(grid, repeat=len(names) - 1):
        last = round(1 - sum(combo), 2)
        if last < 0:
            continue
        w = np.array([*combo, last])
        mae = float(np.mean(np.abs(preds @ w - y)))
        if mae < best_mae - 1e-9:
            best_w, best_mae = w, mae
    return {n: float(v) for n, v in zip(names, best_w, strict=True)}, best_mae


def summarize(df: pd.DataFrame) -> dict:
    """MAE каждой модели, лучший ансамбль всех и CatBoost+GRU, корреляции ошибок."""
    names = [c for c in ("catboost", "lgbm", "gru", "lgbm_v1") if c in df and df[c].notna().all()]
    y = df["y"].to_numpy()
    single = {n: round(float(np.mean(np.abs(df[n] - y))), 2) for n in names}
    w_all, mae_all = best_weights(df, names)
    out = {"folds": "seq_data.fold_ids: GroupKFold(5)x3 по (tr_id, floor(T,30мин))",
           "mode": "наивный (синтетика 0.5 без clone guard) — как OOF GRU",
           "n_real_points": int(df["sample_id"].nunique()), "single_mae": single,
           "weights": w_all, "ensemble_mae": round(mae_all, 2),
           "residual_corr": df[names].sub(y, axis=0).corr().round(3).to_dict()}
    if "gru" in names:
        w2, mae2 = best_weights(df, ["catboost", "gru"])
        out["catboost_gru"] = {"weights": w2, "mae": round(mae2, 2)}
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    metrics = json.loads((MODELS / "metrics.json").read_text("utf-8"))
    weight = metrics["submission"]["best_synthetic_weight"]
    df, extra = own_oof(labeled(), clone_sources(), weight)
    df = attach_external(df)
    report = summarize(df) | extra
    log.info("ensemble: %s", report)
    cols = ["sample_id", "fold_repeat", "catboost", "catboost_q10", "catboost_q90", "y"]
    df[cols].rename(columns={"catboost": "oof_pred", "catboost_q10": "oof_q10",
                             "catboost_q90": "oof_q90"}).round(3).to_csv(
        MODELS / "oof_catboost.csv", index=False)
    metrics["ensemble"] = report
    (MODELS / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=float), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
