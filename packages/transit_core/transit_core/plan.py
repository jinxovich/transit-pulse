"""Плановое расписание без факта.

Единственная точка чтения ``schedule.csv`` / ``schedule_plan.csv`` для признаков и потока.
Колонка ``time_fact_begin`` не читается никогда (``usecols``), поэтому факт физически не
может попасть в признаки.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

NO_ADDRESS = "Остановка без адреса"
TRIP_GAP_MIN = 5.0
PLAN_USECOLS = [
    "tt_action_item_id",
    "tr_id",
    "time_begin",
    "manual_fill",
    "geom",
    "building_address",
]
PLAN_COLUMNS = [
    "visit_id",
    "tr_id",
    "tb",
    "lon",
    "lat",
    "stop_key",
    "name",
    "manual_fill",
    "gap_min",
    "new_trip",
    "trip",
]
_POINT_RE = r"POINT \(([-\d.]+) ([-\d.]+)\)"


def stop_key(geom: str) -> str:
    """Стабильный короткий ключ остановки по её WKT-геометрии.

    Совпадает с ``contracts.mockgen.common.stop_key``.
    """
    return "st_" + hashlib.md5(geom.encode()).hexdigest()[:8]


def _as_bool(col: pd.Series) -> pd.Series:
    """Приводит ``True``/``False`` (строки или bool) к bool."""
    if col.dtype == bool:
        return col
    return col.astype(str).str.strip().str.lower().eq("true")


def prepare_plan(raw: pd.DataFrame) -> pd.DataFrame:
    """Строит таблицу плана из сырых плановых колонок расписания.

    :param raw: кадр с колонками :data:`PLAN_USECOLS` (без факта).
    :return: кадр с колонками :data:`PLAN_COLUMNS`, отсортированный по ``(tr_id, tb)``.
    """
    if "time_fact_begin" in raw.columns:
        raise ValueError("план не должен содержать time_fact_begin")
    s = pd.DataFrame(
        {
            "visit_id": raw["tt_action_item_id"].astype(np.int64),
            "tr_id": raw["tr_id"].astype(np.int64),
            "tb": pd.to_datetime(raw["time_begin"], format="ISO8601").astype("datetime64[ns]"),
            "manual_fill": _as_bool(raw["manual_fill"]),
            "name": raw["building_address"].fillna(NO_ADDRESS).astype(str),
        }
    )
    xy = raw["geom"].str.extract(_POINT_RE).astype(float)
    s["lon"], s["lat"] = xy[0].to_numpy(), xy[1].to_numpy()
    s["stop_key"] = raw["geom"].map(stop_key)
    s = s.sort_values(["tr_id", "tb"], kind="stable").reset_index(drop=True)
    gap = s.groupby("tr_id")["tb"].diff().dt.total_seconds() / 60.0
    s["gap_min"] = gap
    s["new_trip"] = (gap.isna() | (gap >= TRIP_GAP_MIN)).astype(np.int64)
    s["trip"] = s.groupby("tr_id")["new_trip"].cumsum().astype(np.int64)
    return s[PLAN_COLUMNS]


def load_plan(path: Path) -> pd.DataFrame:
    """Читает плановое расписание (train/test ``schedule.csv`` или validate ``schedule_plan.csv``).

    :param path: путь к CSV расписания.
    :return: кадр с колонками ``visit_id, tr_id, tb, lon, lat, stop_key, name, manual_fill,
        gap_min, new_trip, trip``; отсортирован по ``(tr_id, tb)``.
    """
    raw = pd.read_csv(path, usecols=PLAN_USECOLS)
    return prepare_plan(raw)


def split_by_tr(plan: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Разбивает план на кадры по ТС (индекс сброшен)."""
    return {int(k): g.reset_index(drop=True) for k, g in plan.groupby("tr_id", sort=False)}
