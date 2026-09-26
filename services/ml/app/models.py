"""Обёртки моделей: CatBoost MultiQuantile (основная) и LightGBM L1 (сравнение, бленд).

Все модели учат остаток ``y − cur_dev`` (``cur_dev`` NaN → 0).
"""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

QUANTILES = (0.1, 0.5, 0.9)
MAX_ITERS = 2000
STAGE = 100
CB_PARAMS = {
    "loss_function": "MultiQuantile:alpha=" + ",".join(str(q) for q in QUANTILES),
    "learning_rate": 0.05,
    "depth": 6,
    "l2_leaf_reg": 3.0,
    "task_type": "CPU",  # MultiQuantile на GPU не поддерживается (catboost 1.2)
    "verbose": False,
    "allow_writing_files": False,
    "thread_count": 8,
}
LGB_PARAMS = {
    "objective": "l1", "learning_rate": 0.03, "num_leaves": 15, "min_data_in_leaf": 30,
    "bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.8, "verbose": -1,
    "num_threads": 4,  # на маленьких данных много потоков только мешают (конкуренция за CPU)
}
LGB_ROUNDS = 400
LGB_SEEDS = 5


def fit_catboost(x: pd.DataFrame, y: np.ndarray, w: np.ndarray, iterations: int = MAX_ITERS,
                 seed: int = 0) -> CatBoostRegressor:
    """Обучает CatBoost MultiQuantile(0.1/0.5/0.9) на остатке."""
    model = CatBoostRegressor(**CB_PARAMS, iterations=iterations, random_seed=seed)
    model.fit(x, y, sample_weight=w)
    return model


def sort_quantiles(pred: np.ndarray) -> np.ndarray:
    """Убирает пересечение квантилей: сортировка по последней оси."""
    return np.sort(np.asarray(pred, dtype=float), axis=-1)


def staged_quantiles(model: CatBoostRegressor, x: pd.DataFrame) -> np.ndarray:
    """Прогнозы квантилей каждые :data:`STAGE` итераций: (стадии, n, 3)."""
    stages = [sort_quantiles(p) for p in model.staged_predict(x, eval_period=STAGE)]
    return np.stack(stages)


def stage_iterations(n_stages: int) -> np.ndarray:
    """Число итераций, соответствующее каждой стадии :func:`staged_quantiles`."""
    return np.minimum(np.arange(1, n_stages + 1) * STAGE, MAX_ITERS)


def fit_predict_lgbm(x: pd.DataFrame, y: np.ndarray, w: np.ndarray,
                     x_pred: pd.DataFrame) -> np.ndarray:
    """LightGBM L1, усреднение по :data:`LGB_SEEDS` seed (как в v1)."""
    preds = []
    for seed in range(LGB_SEEDS):
        booster = lgb.train({**LGB_PARAMS, "seed": seed}, lgb.Dataset(x, y, weight=w),
                            num_boost_round=LGB_ROUNDS)
        preds.append(booster.predict(x_pred))
    return np.mean(preds, axis=0)
