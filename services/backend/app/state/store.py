"""Состояние флота: кольцевые буферы телеметрии и результаты прогнозов на каждое ТС.

Буфер хранит последние ``history_min`` сим-минут точек (``et, lon, lat, speed, heading,
valid``). Ingest только дописывает точки; пайплайн читает копию трека
(:meth:`VehicleRecord.track_frame`) в отдельном потоке, поэтому блокировки не нужны.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from transit_core import schemas as S

from ..config import DATASET_DAY
from .static import StaticData, route_of

TRACK_COLUMNS = ["et", "lon", "lat", "speed", "heading", "valid"]
WINDOW_BEFORE = timedelta(hours=1)
WINDOW_AFTER = timedelta(hours=24 + 6)
DEV_SERIES_MIN = 60


@dataclass(frozen=True)
class Fix:
    """Последняя валидная координата ТС."""

    lon: float
    lat: float
    heading: float
    speed: float


@dataclass
class VehicleRecord:
    """Всё, что бэкенд знает об одном ТС в текущей сессии."""

    vehicle_id: str
    kind: S.VehicleKind
    unit_id: int
    tr_id: int | None
    route_id: str | None
    route_name: str | None
    points: deque = field(default_factory=deque)
    first_et: datetime | None = None
    last_et: datetime | None = None
    last_wall: float = 0.0
    fix: Fix | None = None
    dirty_wall: float | None = None
    prediction: S.Prediction | None = None
    window_preds: dict[str, float] = field(default_factory=dict)
    current_dev_s: float | None = None
    next_stop: S.StopRef | None = None
    arrivals: dict[str, tuple[datetime, float]] = field(default_factory=dict)
    dev_series: deque = field(default_factory=lambda: deque(maxlen=DEV_SERIES_MIN + 1))
    open_incident_id: str | None = None

    def add(self, point: tuple, wall: float, history: timedelta) -> None:
        """Дописывает точку ``(et, lon, lat, speed, heading, valid)`` и чистит старые."""
        et, lon, lat, speed, heading, valid = point
        self.points.append(point)
        self.first_et = et if self.first_et is None else min(self.first_et, et)
        self.last_et = et if self.last_et is None else max(self.last_et, et)
        self.last_wall = wall
        if self.dirty_wall is None:
            self.dirty_wall = wall
        if valid:
            self.fix = Fix(lon, lat, heading, speed)
        edge = self.last_et - history
        while self.points and self.points[0][0] < edge:
            self.points.popleft()

    def history_min(self) -> float:
        """Сколько сим-минут истории накоплено в сессии."""
        if self.first_et is None or self.last_et is None:
            return 0.0
        return (self.last_et - self.first_et).total_seconds() / 60

    def track_frame(self) -> pd.DataFrame:
        """Копия трека для пайплайна: колонки INTERFACES §3, отсортировано по ``et``."""
        df = pd.DataFrame(list(self.points), columns=TRACK_COLUMNS)
        df["et"] = pd.to_datetime(df["et"])
        df["valid"] = df["valid"].astype(bool)
        return df.sort_values("et", kind="stable").reset_index(drop=True)


def in_dataset_window(et: datetime) -> bool:
    """Время пакета в пределах ``[день − 1 ч, конец дня + 6 ч]``."""
    return DATASET_DAY - WINDOW_BEFORE <= et <= DATASET_DAY + WINDOW_AFTER


class FleetStore:
    """Реестр ТС сессии: классификация бортов и запись точек."""

    def __init__(self, static: StaticData, history_min: int = 60) -> None:
        self.static = static
        self.history = timedelta(minutes=history_min)
        self.vehicles: dict[str, VehicleRecord] = {}

    def classify(self, unit_id: int, et: datetime) -> tuple[str, S.VehicleKind, int | None]:
        """``(vehicle_id, kind, tr_id)``: неизвестный борт или время вне дня — ``unknown``."""
        tr_id = self.static.unit_map.get(unit_id)
        if tr_id is None or not in_dataset_window(et):
            return f"u:{unit_id}", "unknown", None
        kind: S.VehicleKind = "scheduled" if tr_id in self.static.plans else "no_schedule"
        return str(tr_id), kind, tr_id

    def record(self, unit_id: int, et: datetime) -> VehicleRecord:
        """Запись ТС (создаётся при первом пакете)."""
        vid, kind, tr_id = self.classify(unit_id, et)
        rec = self.vehicles.get(vid)
        if rec is None:
            route_id, route_name = route_of(self.static, tr_id)
            rec = VehicleRecord(vid, kind, unit_id, tr_id, route_id, route_name)
            self.vehicles[vid] = rec
        return rec

    def add(self, rec: VehicleRecord, point: tuple, wall: float) -> None:
        """Дописывает точку в буфер ТС."""
        rec.add(point, wall, self.history)

    def remove(self, vehicle_id: str) -> None:
        """Убирает ТС (давно нет данных)."""
        self.vehicles.pop(vehicle_id, None)

    def clear(self) -> None:
        """Новая сессия: всё состояние флота сбрасывается."""
        self.vehicles.clear()
