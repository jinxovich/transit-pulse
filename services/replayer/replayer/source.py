"""История телеметрии для воспроизведения: ``validate/traffic.csv`` в порядке времени."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from transit_core.ndtp import NavCell

CLONE_UNIT_STEP = 10_000_000
"""Клон ``i`` юнита получает ``unit_id + CLONE_UNIT_STEP·i`` (INTERFACES §2)."""
CLONE_SHIFT_DEG = 0.002
"""Сдвиг координат клона ``i`` на ``i·CLONE_SHIFT_DEG`` градуса по обеим осям."""
_U16_MAX = 65535
_COLUMNS = ["unit_id", "event_time", "location_valid", "lon", "lat", "alt", "speed", "heading"]


@dataclass(frozen=True)
class Track:
    """Колонки истории, отсортированные по времени (unix-секунды, наивное время как UTC)."""

    ts: np.ndarray
    unit_id: np.ndarray
    valid: np.ndarray
    lon: np.ndarray
    lat: np.ndarray
    alt: np.ndarray
    speed: np.ndarray
    course: np.ndarray

    def __len__(self) -> int:
        return len(self.ts)

    @property
    def units(self) -> list[int]:
        """Уникальные ``unit_id`` в порядке возрастания."""
        return sorted(int(u) for u in np.unique(self.unit_id))

    def index_at(self, sim_ts: float) -> int:
        """Индекс первой строки с ``ts >= sim_ts``."""
        return int(np.searchsorted(self.ts, sim_ts, side="left"))

    def nav(self, i: int, clone: int = 0) -> NavCell:
        """Навигационная ячейка строки ``i`` (для клона — со сдвигом координат)."""
        if not self.valid[i]:
            return NavCell(timestamp=int(self.ts[i]), lon=0.0, lat=0.0, valid=False)
        shift = clone * CLONE_SHIFT_DEG
        return NavCell(
            timestamp=int(self.ts[i]),
            lon=float(self.lon[i]) + shift,
            lat=float(self.lat[i]) + shift,
            valid=True,
            speed_kmh=int(self.speed[i]),
            speed_max_kmh=int(self.speed[i]),
            course=int(self.course[i]),
            altitude_m=int(self.alt[i]),
        )


def clone_unit_id(unit_id: int, clone: int) -> int:
    """``unit_id`` клона для нагрузочного теста (``FLEET_MULTIPLIER``)."""
    return unit_id + CLONE_UNIT_STEP * clone


def load_track(path: Path) -> Track:
    """Читает ``traffic.csv``: сортировка по ``event_time``, округление вниз до секунды.

    Невалидные строки (``location_valid=False``) остаются в потоке, но их
    координаты и скорость обнуляются при кодировании — как у реального терминала.
    """
    df = pd.read_csv(path, usecols=_COLUMNS)
    event = pd.to_datetime(df["event_time"], format="ISO8601").dt.floor("s")
    df = df.assign(ts=(event - pd.Timestamp(0)) // pd.Timedelta(seconds=1))
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    valid = df["location_valid"].astype(bool) & df["lon"].notna() & df["lat"].notna()

    def _u16(col: str) -> np.ndarray:
        return df[col].fillna(0).round().clip(0, _U16_MAX).astype(np.int64).to_numpy()

    return Track(
        ts=df["ts"].astype(np.int64).to_numpy(),
        unit_id=df["unit_id"].astype(np.int64).to_numpy(),
        valid=valid.to_numpy(),
        lon=df["lon"].fillna(0.0).to_numpy(),
        lat=df["lat"].fillna(0.0).to_numpy(),
        alt=_u16("alt"),
        speed=_u16("speed"),
        course=_u16("heading"),
    )
