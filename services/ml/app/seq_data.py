"""Сборка выборки для GRU: треки из CSV, плановое расписание, тензоры на точки прогноза.

Правило честности: из расписания берутся только плановые колонки (``prepare_plan``),
из телеметрии — только строки с ``event_time <= T`` (это делает ``build_sequence``).
Трек из CSV приводится к виду «как после NDTP»: время вниз до секунды, lon/lat до 1e-7.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from transit_core.sequence import PLAN_COLS, build_sequence, prepare_plan, sequence_static

SYNTHETIC_UNIT_MIN = 9_000_000
BLOCK = "30min"
N_FOLDS = 5
TRACK_COLS = ["tr_id", "unit_id", "event_time", "location_valid", "lon", "lat", "speed", "heading"]


@dataclass(frozen=True)
class SeqSet:
    """Точки прогноза и их тензоры: ``seq (N, 80, F)``, ``static (N, S)``."""

    points: pd.DataFrame
    seq: np.ndarray
    static: np.ndarray


def load_track_csv(path: Path) -> pd.DataFrame:
    """Читает telemetry CSV в формат трека ``et, lon, lat, speed, heading, valid``."""
    t = pd.read_csv(path, usecols=TRACK_COLS, low_memory=False)
    t["et"] = pd.to_datetime(t["event_time"], format="mixed").dt.floor("s")
    t["lon"] = t["lon"].astype(float).round(7)
    t["lat"] = t["lat"].astype(float).round(7)
    t["valid"] = t["location_valid"].astype(str).str.lower().eq("true")
    t = t.sort_values(["tr_id", "et"], kind="stable")
    return t[["tr_id", "unit_id", "et", "lon", "lat", "speed", "heading", "valid"]]


def load_plan_csv(path: Path) -> pd.DataFrame:
    """Читает расписание только по плановым колонкам (факт не загружается вовсе)."""
    return prepare_plan(pd.read_csv(path, usecols=PLAN_COLS))


def synthetic_tr_ids(traffic: pd.DataFrame) -> set[int]:
    """ТС синтетики организаторов: ``unit_id >= 9 000 000``."""
    return set(traffic.loc[traffic["unit_id"] >= SYNTHETIC_UNIT_MIN, "tr_id"].unique())


def build_set(points: pd.DataFrame, plan: pd.DataFrame, traffic: pd.DataFrame) -> SeqSet:
    """Тензоры для всех точек: последовательность и статика строго на момент ``T``."""
    p = points.reset_index(drop=True).copy()
    p["T"] = pd.to_datetime(p["T"])
    p["ttb"] = pd.to_datetime(p["target_time_begin"])
    plans = dict(tuple(plan.groupby("tr_id")))
    tracks = dict(tuple(traffic.groupby("tr_id")))
    empty = traffic.iloc[:0]
    seqs, stats = [], []
    for r in p.itertuples():
        plan_tr, track = plans[r.tr_id], tracks.get(r.tr_id, empty)
        seqs.append(build_sequence(track, r.T, plan_tr))
        stats.append(
            sequence_static(r.T, r.target_stop_id, r.ttb, r.cur_dev_s, plan_tr, track)
        )
    return SeqSet(p, np.stack(seqs), np.stack(stats))


def group_key(points: pd.DataFrame) -> pd.Series:
    """Группа CV: ``(tr_id, floor(T, 30 мин))`` — блок, которым нарезаны точки."""
    block = pd.to_datetime(points["T"]).dt.floor(BLOCK).astype("int64") // 10**9
    return points["tr_id"].astype(str) + "_" + block.astype(str)


def fold_ids(groups: pd.Series, repeat: int, k: int = N_FOLDS) -> np.ndarray:
    """Номер фолда для каждой строки: группы перемешаны seed'ом ``repeat``, раздаются по кругу."""
    uniq = np.sort(groups.unique())
    perm = np.random.default_rng(1000 + repeat).permutation(len(uniq))
    fold_of = dict(zip(uniq[perm], np.arange(len(uniq)) % k, strict=True))
    return groups.map(fold_of).to_numpy()


def load_labeled(raw: Path) -> SeqSet:
    """Train + test с разметкой; ``is_real`` отделяет реальные ТС от синтетики."""
    parts = []
    for split, labels in (("train", "labels_train.csv"), ("test", "labels_test.csv")):
        lb = pd.read_csv(raw / "labels" / labels)
        traffic = load_track_csv(raw / split / "traffic.csv")
        s = build_set(lb, load_plan_csv(raw / split / "schedule.csv"), traffic)
        s.points["split"] = split
        s.points["is_real"] = ~s.points["tr_id"].isin(synthetic_tr_ids(traffic))
        parts.append(s)
    points = pd.concat([s.points for s in parts], ignore_index=True)
    points["group"] = group_key(points)
    return SeqSet(
        points,
        np.concatenate([s.seq for s in parts]),
        np.concatenate([s.static for s in parts]),
    )


def load_validate(raw: Path) -> SeqSet:
    """Validate: points + телеметрия + план без факта; никаких данных из train/test."""
    points = pd.read_csv(raw / "validate" / "points.csv")
    traffic = load_track_csv(raw / "validate" / "traffic.csv")
    return build_set(points, load_plan_csv(raw / "validate" / "schedule_plan.csv"), traffic)


def cached(path: Path, build) -> SeqSet:
    """Кэширует ``SeqSet`` в ``npz`` + ``parquet``-подобный csv рядом, чтобы не пересчитывать."""
    npz, csv = path.with_suffix(".npz"), path.with_suffix(".csv")
    if npz.exists() and csv.exists():
        arr = np.load(npz)
        return SeqSet(pd.read_csv(csv), arr["seq"], arr["static"])
    s = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(npz, seq=s.seq, static=s.static)
    s.points.to_csv(csv, index=False)
    return s
