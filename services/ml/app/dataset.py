"""Сборка обучающих таблиц через тот же :func:`transit_core.features.point_features`.

Офлайн = поток: трек из ``traffic.csv`` приводится к виду после NDTP
(:func:`transit_core.track.load_traffic`), план читается без факта
(:func:`transit_core.plan.load_plan`). Для stream-модели ``cur_dev`` заменяется онлайн-оценкой
:func:`transit_core.stops_detector.online_cur_dev` по треку до ``T``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from transit_core.features import FEATURES, point_features
from transit_core.plan import load_plan, split_by_tr
from transit_core.stops_detector import online_cur_dev
from transit_core.track import load_traffic, split_tracks, synthetic_tr_ids

ROOT = Path(__file__).resolve().parents[3]
RAW = Path(os.environ.get("DATA_DIR", ROOT / "data" / "raw"))
CACHE = ROOT / "data" / "cache"
BLOCK_GAP = pd.Timedelta(minutes=5)

# сплит → (расписание, трафик, файл точек)
SPLITS = {
    "train": ("train/schedule.csv", "train/traffic.csv", "labels/labels_train.csv"),
    "test": ("test/schedule.csv", "test/traffic.csv", "labels/labels_test.csv"),
    "validate": ("validate/schedule_plan.csv", "validate/traffic.csv", "validate/points.csv"),
}


_EMPTY_TRACK = pd.DataFrame({
    "et": np.array([], dtype="datetime64[ns]"), "lon": [], "lat": [], "speed": [],
    "heading": [], "valid": np.array([], dtype=bool),
})


@dataclass(frozen=True)
class Table:
    """Точки сплита с признаками: ``meta`` (id, T, цель, флаги) и матрица ``x``."""

    meta: pd.DataFrame
    x: pd.DataFrame


def _blocks(points: pd.DataFrame) -> pd.Series:
    """Номер 30-минутного блока: подряд идущие точки ТС с шагом 5 мин."""
    order = points.sort_values(["tr_id", "T"])
    new = (order["tr_id"].diff() != 0) | (order["T"].diff() > BLOCK_GAP)
    return new.cumsum().reindex(points.index)


def build_split(name: str) -> Table:
    """Признаки всех точек сплита (подсказка и онлайн-``cur_dev``)."""
    plan_f, traffic_f, points_f = SPLITS[name]
    plans = split_by_tr(load_plan(RAW / plan_f))
    traffic = load_traffic(RAW / traffic_f)
    tracks, synthetic = split_tracks(traffic), synthetic_tr_ids(traffic)
    pts = pd.read_csv(RAW / points_f)
    pts["T"] = pd.to_datetime(pts["T"], format="ISO8601")
    rows, online = [], []
    for r in pts.itertuples():
        t = r.T.to_pydatetime()
        track = tracks.get(r.tr_id, _EMPTY_TRACK)
        rows.append(point_features(plans[r.tr_id], track, t, r.target_stop_id, r.cur_dev_s))
        online.append(online_cur_dev(plans[r.tr_id], track, t))
    meta = pts.drop(columns=[c for c in ("target_class",) if c in pts]).copy()
    meta["split"] = name
    meta["synthetic"] = meta["tr_id"].isin(synthetic)
    meta["cur_dev_online"] = np.array([np.nan if v is None else v for v in online], dtype=float)
    meta["block"] = name + "-" + _blocks(meta).astype(str)
    return Table(meta.reset_index(drop=True), pd.DataFrame(rows, columns=FEATURES))


def load_split(name: str, refresh: bool = False) -> Table:
    """Таблица сплита с кешем в ``data/cache`` (pickle)."""
    path = CACHE / f"{name}.pkl"
    if path.exists() and not refresh:
        return pd.read_pickle(path)
    table = build_split(name)
    CACHE.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(table, path)
    return table


def labeled(refresh: bool = False) -> Table:
    """train + test вместе (реальные и синтетические размеченные точки)."""
    parts = [load_split(s, refresh) for s in ("train", "test")]
    meta = pd.concat([p.meta for p in parts], ignore_index=True)
    x = pd.concat([p.x for p in parts], ignore_index=True)
    return Table(meta, x)


def with_cur_dev(x: pd.DataFrame, meta: pd.DataFrame, mode: str) -> tuple[pd.DataFrame, np.ndarray]:
    """Матрица признаков и база прогноза для режима ``submission`` / ``stream``.

    База = ``cur_dev`` (NaN → 0); модель учит остаток ``y − база``.
    """
    x = x.copy()
    if mode == "stream":
        x["cur_dev"] = meta["cur_dev_online"].to_numpy()
    elif mode != "submission":
        raise ValueError(f"неизвестный режим {mode}")
    base = np.nan_to_num(x["cur_dev"].to_numpy(dtype=float), nan=0.0)
    return x, base


def clone_sources() -> dict[int, int]:
    """Синтетический ТС → реальный источник (максимум общих остановок в плане train)."""
    plan = load_plan(RAW / SPLITS["train"][0])
    synthetic = synthetic_tr_ids(load_traffic(RAW / SPLITS["train"][1]))
    keys = plan.groupby("tr_id")["stop_key"].apply(set)
    real = [int(k) for k in keys.index if k not in synthetic]
    return {int(c): max(real, key=lambda r: len(keys[c] & keys[r]))
            for c in keys.index if c in synthetic}
