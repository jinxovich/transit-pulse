"""Обучение v2: CV (baseline / LightGBM v1 / CatBoost), выбор веса синтетики, финальные модели.

Запуск (≈20 мин на CPU)::

    uv run python -m services.ml.app.train

Артефакты в ``models/``: ``catboost_submission.cbm``, ``catboost_stream.cbm``,
``feature_list.json``, ``metrics.json``, ``calibration_{mode}.json`` (калибровка p_late и
интервалов по lead, :mod:`services.ml.app.calibration`), ``oof_catboost_honest.csv``,
``cv_folds.csv`` (OOF для ансамбля на фолдах GRU — ``services.ml.app.ensemble`` →
``oof_catboost.csv``).
"""

from __future__ import annotations

import json
import logging
import sys

import numpy as np
import pandas as pd

from services.ml.app import calibration, cv
from services.ml.app import models as M
from services.ml.app.dataset import CACHE, ROOT, clone_sources, labeled, with_cur_dev
from services.ml.app.v1_compare import v1_cv
from transit_core.features import FEATURES

MODELS = ROOT / "models"
SYN_WEIGHTS = (0.0, 0.3, 0.5, 1.0)
FINAL_ITER_FACTOR = 1.1
TARGET_COVERAGE = 0.8
log = logging.getLogger("train")


def _cb_fit_predict(x_tr, y_tr, w_tr, x_val) -> np.ndarray:
    """CatBoost для CV: квантили по стадиям, ось 0 — точки: (n, стадии, 3)."""
    model = M.fit_catboost(x_tr, y_tr, w_tr)
    return np.transpose(M.staged_quantiles(model, x_val), (1, 0, 2))


def _lgb_fit_predict(x_tr, y_tr, w_tr, x_val) -> np.ndarray:
    return M.fit_predict_lgbm(x_tr, y_tr, w_tr, x_val)


def _curve(oof: np.ndarray, base: np.ndarray, y: np.ndarray) -> np.ndarray:
    """MAE медианы по стадиям (среднее по повторам)."""
    err = np.abs(base[None, :, None] + oof[:, :, :, 1] - y[None, :, None])
    return err.mean(axis=(0, 1))


def _fold_best_iters(oof, base, y, folds) -> list[int]:
    """Лучшая по MAE стадия в каждом (повтор, фолд) → число итераций."""
    iters = M.stage_iterations(oof.shape[2])
    out = []
    for r, fold_ids in enumerate(folds):
        for f in range(cv.N_SPLITS):
            m = fold_ids == f
            err = np.abs(base[m, None] + oof[r, m, :, 1] - y[m, None]).mean(axis=0)
            out.append(int(iters[int(np.argmin(err))]))
    return out


def catboost_cv(x, base, y_all, meta, sources, weight, honest=True, scheme="block") -> dict:
    """CV CatBoost с данным весом синтетики: MAE, лучшие итерации, OOF на лучшей стадии."""
    real = cv.real_positions(meta)
    oof = cv.run_cv(_cb_fit_predict, x, y_all - base, meta, weight, sources, honest, scheme)
    b, y = base[real], y_all[real]
    curve = _curve(oof, b, y)
    stage = int(np.argmin(curve))
    best = _fold_best_iters(oof, b, y, cv.make_folds(meta, scheme))
    q = oof[:, :, stage, :]
    return {"mae": float(curve[stage]), "iters_common": int(M.stage_iterations(len(curve))[stage]),
            "iters_fold_mean": float(np.mean(best)), "oof": q}


def _calibration(q: np.ndarray, base: np.ndarray, y: np.ndarray) -> dict:
    """Калибровка по OOF: множитель ошибки ``k`` и растяжение интервала ``interval_scale``.

    ``expected_abs_error_s = k·(q90−q10)/2`` по сырым квантилям. Сырые q10/q90 у
    MultiQuantile узкие, поэтому их отступ от медианы растягивается в ``interval_scale`` раз,
    чтобы доля попаданий в [q10, q90] на OOF была ≈ 80%.
    """
    half = (q[..., 2] - q[..., 0]) / 2
    mae = np.abs(base + q[..., 1] - y).mean()
    med = base + q[..., 1]
    lo, hi = q[..., 1] - q[..., 0], q[..., 2] - q[..., 1]

    def cover(s: float) -> float:
        return float(((med - s * lo <= y) & (y <= med + s * hi)).mean())

    grid = np.round(np.arange(1.0, 5.01, 0.05), 2)
    scale = float(grid[int(np.argmin([abs(cover(s) - TARGET_COVERAGE) for s in grid]))])
    return {"half_width_mean": float(half.mean()), "k": float(mae / half.mean()),
            "coverage_q10_q90_raw": cover(1.0), "interval_scale": scale,
            "coverage_q10_q90_scaled": cover(scale)}


def _calibrate(mode, q, base, y, lead, folds) -> dict:
    """Старая калибровка + отчёт :mod:`calibration` (до/после); пишет ``calibration_{mode}.json``.

    OOF кешируется в ``data/cache/oof_{mode}.npz`` для пересчёта без переобучения.
    """
    save_oof_cache(mode, q, base, y, lead, folds)
    old = _calibration(q, base, y)
    art, report = calibration.evaluate(q, base, y, lead, folds, old["interval_scale"])
    calibration.write_artifact(mode, art, MODELS)
    return {**old, **report}


def _blend(q50_cb, pred_lgb, base, y) -> dict:
    """Лучший вес CatBoost в бленде с LightGBM по OOF (сетка 0..1)."""
    grid = np.linspace(0, 1, 11)
    maes = [cv.oof_mae(a * q50_cb + (1 - a) * pred_lgb, base, y) for a in grid]
    i = int(np.argmin(maes))
    return {"weight_catboost": float(grid[i]), "mae": float(maes[i]),
            "grid": {f"{a:.1f}": round(m, 2) for a, m in zip(grid, maes, strict=True)}}


def evaluate_mode(mode, table, sources, weights=SYN_WEIGHTS,
                  naive=True) -> tuple[dict, np.ndarray, np.ndarray]:
    """CV-сравнения для режима ``submission`` или ``stream`` по весам синтетики ``weights``."""
    x, base = with_cur_dev(table.x, table.meta, mode)
    y_all = table.meta["target_delay_s"].to_numpy(dtype=float)
    real = cv.real_positions(table.meta)
    res = {"baseline_cur_dev": float(np.mean(np.abs(base[real] - y_all[real])))}
    runs = {}
    for w in weights:
        runs[w] = catboost_cv(x, base, y_all, table.meta, sources, w)
        log.info("%s catboost w=%.1f honest MAE %.2f", mode, w, runs[w]["mae"])
    best_w = min((w for w in weights if w > 0), key=lambda w: runs[w]["mae"])
    naive_mae = (catboost_cv(x, base, y_all, table.meta, sources, best_w, honest=False)["mae"]
                 if naive else None)
    lgb_oof = cv.run_cv(_lgb_fit_predict, x, y_all - base, table.meta, best_w, sources)
    q = runs[best_w]["oof"]
    res.update({
        "catboost": {f"w={w}": {k: v for k, v in r.items() if k != "oof"} for w, r in runs.items()},
        "best_synthetic_weight": best_w,
        "catboost_real_only_mae": runs[0.0]["mae"],
        "catboost_synthetic_mae": runs[best_w]["mae"],
        "catboost_synthetic_naive_cv_mae": naive_mae,
        "lgbm_new_features_mae": cv.oof_mae(lgb_oof, base[real], y_all[real]),
        "blend": _blend(q[..., 1], lgb_oof, base[real], y_all[real]),
        "calibration": _calibrate(mode, q, base[real], y_all[real],
                                  x["lead"].to_numpy(dtype=float)[real],
                                  np.stack(cv.make_folds(table.meta))),
        "final_iterations": int(round(FINAL_ITER_FACTOR * runs[best_w]["iters_fold_mean"])),
    })
    return res, q, lgb_oof


def save_oof_cache(mode: str, q: np.ndarray, base: np.ndarray, y: np.ndarray,
                   lead: np.ndarray, folds: np.ndarray) -> None:
    """OOF-квантили остатка на реальных точках → ``data/cache/oof_{mode}.npz`` (калибровка)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(CACHE / f"oof_{mode}.npz", q=q, base=base, y=y, lead=lead, folds=folds)


def fit_final(mode, table, weight, iterations) -> M.CatBoostRegressor:
    """Финальная модель на train+test (все размеченные точки)."""
    x, base = with_cur_dev(table.x, table.meta, mode)
    y = table.meta["target_delay_s"].to_numpy(dtype=float) - base
    w = np.where(table.meta["synthetic"].to_numpy(), weight, 1.0)
    model = M.fit_catboost(x[FEATURES], y, w, iterations=iterations)
    model.save_model(str(MODELS / f"catboost_{mode}.cbm"))
    return model


def save_oof(table, q, lgb_oof, blend_w) -> None:
    """OOF CatBoost (абсолютная задержка) и схема фолдов — для ансамбля с другими моделями."""
    meta = table.meta.iloc[cv.real_positions(table.meta)]
    _, base = with_cur_dev(table.x, table.meta, "submission")
    b = base[cv.real_positions(table.meta)]
    rows, folds = [], []
    for r, fold_ids in enumerate(cv.make_folds(table.meta)):
        rows.append(pd.DataFrame({
            "sample_id": meta["sample_id"].to_numpy(), "fold_repeat": r, "fold": fold_ids,
            "oof_pred": b + q[r, :, 1], "oof_q10": b + q[r, :, 0], "oof_q90": b + q[r, :, 2],
            "oof_lgbm": b + lgb_oof[r],
            "oof_blend": b + blend_w * q[r, :, 1] + (1 - blend_w) * lgb_oof[r],
            "y": meta["target_delay_s"].to_numpy()}))
        folds.append(pd.DataFrame({"sample_id": meta["sample_id"].to_numpy(),
                                   "block": meta["block"].to_numpy(), "repeat": r,
                                   "fold": fold_ids}))
    pd.concat(rows).round(3).to_csv(MODELS / "oof_catboost_honest.csv", index=False)
    pd.concat(folds).to_csv(MODELS / "cv_folds.csv", index=False)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    MODELS.mkdir(exist_ok=True)
    table, sources = labeled(), clone_sources()
    metrics = {"n_real_points": int((~table.meta["synthetic"]).sum()),
               "n_synthetic_points": int(table.meta["synthetic"].sum()),
               "cv": {"scheme": "GroupKFold 3x5 по (tr_id, 30-мин блок), реальные точки",
                      "clone_guard_min": cv.CLONE_GUARD.total_seconds() / 60},
               "v1_lgbm": v1_cv(table, sources)}
    log.info("v1: %s", metrics["v1_lgbm"])
    sub, q, lgb_oof = evaluate_mode("submission", table, sources)
    save_oof(table, q, lgb_oof, sub["blend"]["weight_catboost"])
    # stream: вес синтетики берём из submission, плюс real-only для сравнения
    stream, _, _ = evaluate_mode("stream", table, sources,
                                 weights=(0.0, sub["best_synthetic_weight"]), naive=False)
    for mode, res in (("submission", sub), ("stream", stream)):
        metrics[mode] = res
        fit_final(mode, table, res["best_synthetic_weight"], res["final_iterations"])
        log.info("%s: %s", mode, {k: v for k, v in res.items() if k != "catboost"})
    (MODELS / "feature_list.json").write_text(json.dumps(FEATURES, indent=2), "utf-8")
    (MODELS / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, default=float), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
