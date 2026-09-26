"""Повторяемый GroupKFold (3×5) по ``(tr_id, 30-мин блок)`` на реальных размеченных точках.

Синтетика (клоны реальных ТС) попадает только в обучающие фолды. В «честном» режиме из
обучения фолда убираются точки клонов тех ТС, что есть в валидационном фолде, в пределах
:data:`CLONE_GUARD` от валидационных точек: у клона задержки коррелируют с источником на
0.88–0.99, и без этого фильтра CV завышает пользу синтетики.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

N_SPLITS = 5
REPEATS = 3
SEED = 20260927
CLONE_GUARD = pd.Timedelta(minutes=45)

FitPredict = Callable[[pd.DataFrame, np.ndarray, np.ndarray, pd.DataFrame], np.ndarray]


def real_positions(meta: pd.DataFrame) -> np.ndarray:
    """Позиции реальных (не синтетических) точек в таблице."""
    return np.flatnonzero(~meta["synthetic"].to_numpy())


def make_folds(meta: pd.DataFrame) -> list[np.ndarray]:
    """Номер фолда для каждой реальной точки, по одному массиву на повтор.

    Группы (блоки) перемешиваются своим seed на повтор и раздаются по фолдам по кругу.
    """
    groups = meta["block"].to_numpy()[real_positions(meta)]
    uniq = np.unique(groups)
    out = []
    for r in range(REPEATS):
        order = np.random.default_rng(SEED + r).permutation(uniq)
        fold_of = {g: i % N_SPLITS for i, g in enumerate(order)}
        out.append(np.array([fold_of[g] for g in groups]))
    return out


def allowed_synthetic(meta: pd.DataFrame, val_pos: np.ndarray, sources: dict[int, int],
                      honest: bool) -> np.ndarray:
    """Позиции синтетических точек, разрешённых для обучения фолда."""
    syn = np.flatnonzero(meta["synthetic"].to_numpy())
    if not honest:
        return syn
    val = meta.iloc[val_pos]
    keep = np.ones(len(syn), dtype=bool)
    syn_meta = meta.iloc[syn]
    src = syn_meta["tr_id"].map(sources).to_numpy()
    t_syn = syn_meta["T"].to_numpy()
    guard = CLONE_GUARD.to_timedelta64()
    for s, g in val.groupby("tr_id"):
        mask = src == s
        if not mask.any():
            continue
        gap = np.abs(t_syn[mask][:, None] - g["T"].to_numpy()[None, :]).min(axis=1)
        keep[np.flatnonzero(mask)[gap <= guard]] = False
    return syn[keep]


def run_cv(fit_predict: FitPredict, x: pd.DataFrame, y_res: np.ndarray, meta: pd.DataFrame,
           syn_weight: float, sources: dict[int, int], honest: bool = True) -> np.ndarray:
    """OOF-прогнозы остатка на реальных точках: массив (повтор, n_real, ...).

    :param fit_predict: ``(x_tr, y_tr, w_tr, x_val) -> прогноз`` (ось 0 — точки).
    :param syn_weight: вес синтетики; 0 — обучение только на реальных.
    """
    real = real_positions(meta)
    oof = None
    for r, fold_ids in enumerate(make_folds(meta)):
        for f in range(N_SPLITS):
            val, tr_real = real[fold_ids == f], real[fold_ids != f]
            syn = allowed_synthetic(meta, val, sources, honest) if syn_weight > 0 else []
            tr = np.concatenate([tr_real, syn]).astype(int)
            w = np.where(meta["synthetic"].to_numpy()[tr], syn_weight, 1.0)
            pred = fit_predict(x.iloc[tr], y_res[tr], w, x.iloc[val])
            if oof is None:
                oof = np.full((REPEATS, len(real), *pred.shape[1:]), np.nan)
            oof[r, fold_ids == f] = pred
    return oof


def oof_mae(pred_res: np.ndarray, base: np.ndarray, y: np.ndarray) -> float:
    """MAE абсолютного прогноза ``база + остаток``, среднее по повторам (ось 0)."""
    return float(np.mean([np.mean(np.abs(base + p - y)) for p in pred_res]))
