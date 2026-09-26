"""Инференс без веб-обвязки: загрузка моделей, квантили, p_late, вклады признаков.

Модель предсказывает остаток ``delay − cur_dev`` квантилями 0.1/0.5/0.9. Абсолютный прогноз =
``cur_dev`` (NaN → 0) + квантиль; q10/q90 растянуты от медианы множителем
``interval_scale`` из калибровки (покрытие 80% на OOF). Вклад ``cur_dev`` в ответе включает
саму базу, так что сумма вкладов по всем признакам + ``expected_value`` = ``delay_s``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from catboost import CatBoostRegressor, Pool

LATE_S = 120.0
TOP_K = 5
MODES = ("stream", "submission")
DEFAULT_ERR_K = 1.0
_EPS = 1e-6


@dataclass
class LoadedModel:
    """Модель одного режима и её калибровка."""

    mode: str
    model: CatBoostRegressor
    err_k: float
    version: str
    interval_scale: float = 1.0


@dataclass
class Registry:
    """Все загруженные модели, список признаков и метрики из ``metrics.json``."""

    features: list[str]
    models: dict[str, LoadedModel] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)


def load_registry(models_dir: Path) -> Registry:
    """Читает ``feature_list.json``, ``metrics.json`` и ``catboost_{mode}.cbm``."""
    features = json.loads((models_dir / "feature_list.json").read_text("utf-8"))
    metrics_path = models_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text("utf-8")) if metrics_path.exists() else {}
    reg = Registry(features=features, metrics=metrics)
    for mode in MODES:
        path = models_dir / f"catboost_{mode}.cbm"
        if not path.exists():
            continue
        model = CatBoostRegressor()
        model.load_model(str(path))
        cal = metrics.get(mode, {}).get("calibration", {})
        reg.models[mode] = LoadedModel(mode, model, float(cal.get("k", DEFAULT_ERR_K)),
                                       f"catboost-v2-{mode}",
                                       float(cal.get("interval_scale", 1.0)))
    if not reg.models:
        raise FileNotFoundError(f"в {models_dir} нет моделей catboost_*.cbm")
    return reg


def to_matrix(rows: list[dict[str, float | None]], features: list[str]) -> np.ndarray:
    """Словари признаков → матрица (n, F); отсутствующие и None → NaN."""
    x = np.full((len(rows), len(features)), np.nan)
    for i, row in enumerate(rows):
        for j, name in enumerate(features):
            v = row.get(name)
            if v is not None:
                x[i, j] = float(v)
    return x


def p_exceed(q: np.ndarray, threshold: float = LATE_S) -> np.ndarray:
    """P(delay > threshold) по линейной интерполяции CDF через (q10,.1),(q50,.5),(q90,.9).

    Хвосты продолжаются с наклоном соседнего отрезка и обрезаются в [0, 1].
    """
    q10, q50, q90 = q[:, 0], q[:, 1], q[:, 2]
    lo_slope = 0.4 / np.maximum(q50 - q10, _EPS)
    hi_slope = 0.4 / np.maximum(q90 - q50, _EPS)
    x = threshold
    cdf = np.where(
        x <= q50,
        0.1 + (x - q10) * lo_slope,
        0.5 + (x - q50) * hi_slope,
    )
    cdf = np.where(x > q90, 0.9 + (x - q90) * hi_slope, cdf)
    cdf = np.where(x < q10, 0.1 - (q10 - x) * lo_slope, cdf)
    return 1.0 - np.clip(cdf, 0.0, 1.0)


def widen(q: np.ndarray, scale: float) -> np.ndarray:
    """Растягивает q10/q90 от медианы в ``scale`` раз (калибровка покрытия по OOF)."""
    out = q.copy()
    out[:, 0] = q[:, 1] - scale * (q[:, 1] - q[:, 0])
    out[:, 2] = q[:, 1] + scale * (q[:, 2] - q[:, 1])
    return out


def top_contributions(shap_row: np.ndarray, base: float, features: list[str],
                      cur_dev_idx: int | None) -> list[dict]:
    """Топ-``TOP_K`` вкладов по модулю (секунды); к ``cur_dev`` добавлена база."""
    contrib = shap_row.copy()
    if cur_dev_idx is not None:
        contrib[cur_dev_idx] += base
    order = np.argsort(-np.abs(contrib))[:TOP_K]
    return [{"feature": features[j], "contribution_s": round(float(contrib[j]), 2)}
            for j in order]


def predict(lm: LoadedModel, x: np.ndarray, features: list[str],
            with_contributions: bool = True) -> list[dict]:
    """Батч-прогноз: delay_s, q10, q90, p_late, expected_abs_error_s, contributions."""
    cur_idx = features.index("cur_dev") if "cur_dev" in features else None
    base = np.zeros(len(x)) if cur_idx is None else np.nan_to_num(x[:, cur_idx], nan=0.0)
    q = np.sort(np.asarray(lm.model.predict(x), dtype=float).reshape(len(x), -1), axis=1)
    q_abs = widen(q, lm.interval_scale) + base[:, None]
    p_late = p_exceed(q_abs)
    err = lm.err_k * (q[:, 2] - q[:, 0]) / 2
    shap = None
    if with_contributions:
        sv = lm.model.get_feature_importance(Pool(x), type="ShapValues")
        shap = sv[:, 1, :-1] if sv.ndim == 3 else sv[:, :-1]
    out = []
    for i in range(len(x)):
        item = {"delay_s": round(float(q_abs[i, 1]), 2), "q10": round(float(q_abs[i, 0]), 2),
                "q90": round(float(q_abs[i, 2]), 2), "p_late": round(float(p_late[i]), 4),
                "expected_abs_error_s": round(float(err[i]), 2), "contributions": []}
        if shap is not None:
            item["contributions"] = top_contributions(shap[i], base[i], features, cur_idx)
        out.append(item)
    return out
