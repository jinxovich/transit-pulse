"""Вес ансамбля GRU + CatBoost по OOF-прогнозам (только реальные ТС).

Оба файла: ``sample_id, fold_repeat, oof_pred, y``. Если у обоих есть одинаковые
``fold_repeat`` — стыкуем по паре ``(sample_id, fold_repeat)``, иначе усредняем OOF по
повторам и стыкуем по ``sample_id``. Прогноз ансамбля ``w·gru + (1−w)·catboost``,
``w`` — по сетке 0..1 с шагом 0.05.

Запуск::

    uv run python -m scripts.ensemble_weights [--gru models/oof_gru.csv] [--catboost PATH]
    uv run python -m scripts.ensemble_weights --update-metrics   # обновить gru_metrics.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
GRID = np.round(np.linspace(0.0, 1.0, 21), 2)


def _read_catboost(path: Path) -> pd.DataFrame | None:
    """OOF CatBoost из файла (``None``, если файла нет)."""
    return pd.read_csv(path) if path.exists() else None


def _align(gru: pd.DataFrame, cb: pd.DataFrame) -> pd.DataFrame:
    """Таблица ``y, gru, cb`` на общих точках."""
    keys = ["sample_id", "fold_repeat"]
    same_reps = "fold_repeat" in cb and set(gru["fold_repeat"]) == set(cb["fold_repeat"])
    if not same_reps:
        keys = ["sample_id"]
        gru = gru.groupby("sample_id", as_index=False)[["oof_pred", "y"]].mean()
        cb = cb.groupby("sample_id", as_index=False)[["oof_pred", "y"]].mean()
    m = gru.merge(cb, on=keys, suffixes=("_gru", "_cb"))
    return pd.DataFrame({"y": m["y_gru"], "gru": m["oof_pred_gru"], "cb": m["oof_pred_cb"]})


def _corr(a: pd.Series, b: pd.Series) -> float:
    return float(np.corrcoef(a, b)[0, 1])


def weights_table(df: pd.DataFrame) -> pd.DataFrame:
    """MAE ансамбля для каждого ``w`` на сетке."""
    mae = [float((df["y"] - (w * df["gru"] + (1 - w) * df["cb"])).abs().mean()) for w in GRID]
    return pd.DataFrame({"w_gru": GRID, "mae": mae})


def ensemble_report(gru_path: Path, cb_path: Path) -> dict:
    """Лучший вес и MAE ансамбля против второй модели (по умолчанию CatBoost).

    ``mae_other``/``gain_vs_other`` — MAE второй модели и выигрыш ансамбля над ней, сек.
    """
    cb = _read_catboost(cb_path)
    if cb is None:
        return {"status": "oof_catboost.csv не найден — запустите scripts.ensemble_weights позже"}
    df = _align(pd.read_csv(gru_path), cb)
    table = weights_table(df)
    best = table.loc[table["mae"].idxmin()]
    mae_cb = float((df["y"] - df["cb"]).abs().mean())
    return {
        "status": "ok",
        "n_points": int(len(df)),
        "mae_other": round(mae_cb, 2),
        "mae_gru": round(float((df["y"] - df["gru"]).abs().mean()), 2),
        "best_w_gru": float(best["w_gru"]),
        "mae_ensemble": round(float(best["mae"]), 2),
        "gain_vs_other": round(mae_cb - float(best["mae"]), 2),
        "corr_residuals": round(_corr(df["y"] - df["gru"], df["y"] - df["cb"]), 3),
    }


def _verdict(report: dict, name: str) -> str:
    gain = report.get("gain_vs_other")
    if gain is None:
        return f"не оценён ({name}: нет OOF)"
    word = "дал" if gain > 0 else "не дал"
    mae, ens, w = report["mae_other"], report["mae_ensemble"], report["best_w_gru"]
    return f"{word}: {name} {mae} → ансамбль {ens} (w_gru={w})"


def ensemble_section(gru_path: Path, models: Path) -> dict:
    """Ансамбль с CatBoost и с прокси LightGBM quick_v1 + вердикт «прирост дал/не дал»."""
    cb = ensemble_report(gru_path, models / "oof_catboost.csv")
    lgbm_path = models / "oof_lgbm_quick.csv"
    proxy = ensemble_report(gru_path, lgbm_path) if lgbm_path.exists() else {"status": "нет файла"}
    return {
        "ensemble": {"catboost": cb, "proxy_lightgbm_quick_v1": proxy},
        "gain": {
            "vs_catboost": _verdict(cb, "CatBoost"),
            "vs_lightgbm_quick_v1": _verdict(proxy, "LightGBM quick_v1"),
        },
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--gru", type=Path, default=REPO_ROOT / "models" / "oof_gru.csv")
    p.add_argument("--catboost", type=Path, default=REPO_ROOT / "models" / "oof_catboost.csv")
    p.add_argument("--update-metrics", action="store_true", help="дописать в gru_metrics.json")
    args = p.parse_args(argv)
    if not args.update_metrics:
        print(json.dumps(ensemble_report(args.gru, args.catboost), ensure_ascii=False, indent=2))
        return 0
    section = ensemble_section(args.gru, args.catboost.parent)
    path = args.catboost.parent / "gru_metrics.json"
    metrics = json.loads(path.read_text("utf-8")) if path.exists() else {}
    path.write_text(json.dumps({**metrics, **section}, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(section, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
