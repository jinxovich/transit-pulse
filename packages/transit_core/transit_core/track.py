"""Телеметрия: чтение ``traffic.csv`` в том же виде, в каком её видит поток NDTP.

После NDTP время — целые секунды, координаты — шаг 1e-7 градуса, скорость и курс — целые.
Офлайн-признаки строятся на треке, приведённом к тому же виду, чтобы офлайн и поток совпадали.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TRACK_COLUMNS = ["et", "lon", "lat", "speed", "heading", "valid"]
SYNTHETIC_UNIT_MIN = 9_000_000
_USECOLS = ["tr_id", "unit_id", "event_time", "location_valid", "lon", "lat", "speed", "heading"]

# Приближение equirectangular для Москвы: метров в градусе по широте/долготе.
M_PER_DEG_LAT = 111_320.0
LAT0_COS = float(np.cos(np.deg2rad(55.75)))
M_PER_DEG_LON = M_PER_DEG_LAT * LAT0_COS


def to_xy(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Переводит градусы в локальные метры (плоская аппроксимация, точна в пределах города)."""
    return np.asarray(lon) * M_PER_DEG_LON, np.asarray(lat) * M_PER_DEG_LAT


def ndtp_normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Приводит сырые строки телеметрии к виду после NDTP.

    Невалидные строки получают нулевые координаты/скорость — как у настоящего терминала.
    """
    valid = raw["location_valid"].astype(str).str.lower().eq("true").to_numpy()
    et = pd.to_datetime(raw["event_time"], format="ISO8601").dt.floor("s")
    lon = np.where(valid, np.round(raw["lon"].to_numpy(dtype=float), 7), 0.0)
    lat = np.where(valid, np.round(raw["lat"].to_numpy(dtype=float), 7), 0.0)
    speed = np.where(valid, np.nan_to_num(raw["speed"].to_numpy(dtype=float)), 0.0)
    heading = np.where(valid, np.nan_to_num(raw["heading"].to_numpy(dtype=float)), 0.0)
    return pd.DataFrame({
        "tr_id": raw["tr_id"].to_numpy(dtype=np.int64),
        "unit_id": raw["unit_id"].to_numpy(dtype=np.int64),
        "et": et.to_numpy(dtype="datetime64[ns]"),
        "lon": lon,
        "lat": lat,
        "speed": np.trunc(speed),
        "heading": np.trunc(heading),
        "valid": valid,
    })


def load_traffic(path: Path) -> pd.DataFrame:
    """Читает ``traffic.csv`` и нормализует как NDTP; сортировка по ``(tr_id, et)``."""
    raw = pd.read_csv(path, usecols=_USECOLS, low_memory=False)
    df = ndtp_normalize(raw)
    return df.sort_values(["tr_id", "et"], kind="stable").reset_index(drop=True)


def split_tracks(traffic: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Трек каждого ТС с колонками :data:`TRACK_COLUMNS`."""
    return {
        int(k): g[TRACK_COLUMNS].reset_index(drop=True)
        for k, g in traffic.groupby("tr_id", sort=False)
    }


def synthetic_tr_ids(traffic: pd.DataFrame) -> set[int]:
    """ТС-клоны организаторов (``unit_id >= 9_000_000``)."""
    return set(traffic.loc[traffic["unit_id"] >= SYNTHETIC_UNIT_MIN, "tr_id"].astype(int))
