"""Дамп потокового прогона для офлайн-реплея политики алертов и калибровки.

Для каждого прогноза окна ``(T+10, T+15]`` сохраняются признаки, собранные backend'ом на
потоке, ответ финальной модели и **честный** ответ фолд-модели: квантили остатка, база
``cur_dev``, прогноз, p_late и интервал. Фолд выбирается по ближайшей (≤ 30 мин) точке
разметки того же ТС — модель этого фолда не видела ни её, ни клонов рядом с ней. Моменты
без разметки рядом (блоки validate) прогнозирует модель фолда 0: их меток в обучении нет.

Дамп — pickle со словарём таблиц ``preds``, ``minutes``, ``incidents``, ``labels``.
"""

from __future__ import annotations

import json
import pickle
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.stream_eval_honest import FoldModels
from scripts.stream_eval_run import StreamLog
from services.ml.app.inference import LATE_S, p_exceed, widen

NEAREST_LABEL = timedelta(minutes=30)
DEFAULT_FOLD = 0


def assign_folds(preds: pd.DataFrame, fm: FoldModels) -> np.ndarray:
    """Номер фолда для каждого прогноза: фолд ближайшей точки разметки того же ТС."""
    meta = fm.meta.assign(fold=fm.fold_of.loc[fm.meta["sample_id"]].to_numpy())
    out = np.full(len(preds), DEFAULT_FOLD, dtype=int)
    for tr_id, idx in preds.groupby("tr_id").indices.items():
        lab = meta[meta["tr_id"] == tr_id].sort_values("T")
        if lab.empty:
            continue
        lt = lab["T"].to_numpy("datetime64[ns]")
        t = preds["T"].to_numpy("datetime64[ns]")[idx]
        pos = np.clip(np.searchsorted(lt, t), 1, len(lt)) - 1
        nxt = np.clip(pos + 1, 0, len(lt) - 1)
        use_next = np.abs(lt[nxt] - t) < np.abs(lt[pos] - t)
        near = np.where(use_next, nxt, pos)
        close = np.abs(lt[near] - t) <= np.timedelta64(NEAREST_LABEL)
        out[idx] = np.where(close, lab["fold"].to_numpy()[near], DEFAULT_FOLD)
    return out


def honest_window(
    preds: pd.DataFrame, features: dict, fm: FoldModels, scale: float
) -> pd.DataFrame:
    """Честные квантили, прогноз, p_late и интервал для каждого прогноза окна."""
    folds = assign_folds(preds, fm)
    q = np.full((len(preds), 3), np.nan)
    base = np.zeros(len(preds))
    for f in np.unique(folds):
        idx = np.flatnonzero(folds == f)
        rows = [features[(int(r.tr_id), r.T, int(r.visit_id))]
                for r in preds.iloc[idx].itertuples()]  # fmt: skip
        q[idx], base[idx] = fm.residual_quantiles(int(f), rows)
    q_abs = widen(q, scale) + base[:, None]
    return pd.DataFrame({
        "h_fold": folds, "h_base": base,
        "h_q10r": q[:, 0], "h_q50r": q[:, 1], "h_q90r": q[:, 2],
        "h_delay": q_abs[:, 1], "h_q10": q_abs[:, 0], "h_q90": q_abs[:, 2],
        "h_p_late": p_exceed(q_abs, LATE_S),
    }, index=preds.index)  # fmt: skip


def build_dump(log: StreamLog, labels: pd.DataFrame, fm: FoldModels, models_dir: Path) -> dict:
    """Таблицы дампа: прогнозы с признаками и честным ответом, минуты ТС, инциденты, метки."""
    scale = json.loads((models_dir / "metrics.json").read_text("utf-8"))["stream"]
    scale = float(scale.get("calibration", {}).get("interval_scale", 1.0))
    preds = pd.DataFrame(log.preds)
    preds["T"] = pd.to_datetime(preds["T"])
    feats = pd.DataFrame([log.features[(int(r.tr_id), r.T.to_pydatetime(), int(r.visit_id))]
                          for r in preds.itertuples()]).add_prefix("f_")  # fmt: skip
    keyed = {(k[0], pd.Timestamp(k[1]), k[2]): v for k, v in log.features.items()}
    honest = honest_window(preds, keyed, fm, scale)
    return {
        "preds": pd.concat([preds, feats.set_index(preds.index), honest], axis=1),
        "minutes": pd.DataFrame(log.vehicle_minutes),
        "incidents": pd.DataFrame(log.incidents),
        "labels": labels,
        "interval_scale": scale,
    }


def write_dump(path: Path, dump: dict) -> None:
    """Сохраняет дамп (pickle: без лишних зависимостей в окружении)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(dump, fh, protocol=pickle.HIGHEST_PROTOCOL)


def read_dump(path: Path) -> dict:
    """Читает дамп, записанный :func:`write_dump`."""
    with path.open("rb") as fh:
        return pickle.load(fh)
