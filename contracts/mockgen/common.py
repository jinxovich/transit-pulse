"""Константы сессии, загрузка данных и общие хелперы генератора моков."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import TypeAdapter

from transit_core import schemas as S

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "validate"
OUT = ROOT / "contracts" / "fixtures"

SESSION_ID = "mock-0001"
SIM_START = datetime(2026, 1, 6, 7, 0, 0)
STEP = timedelta(seconds=10)
SIM_SPEED = 10.0  # мок-сервер играет один кадр (10 сим-секунд) в секунду
N_FRAMES = 180
WARMUP_FRAMES = 6
DEGRADED = range(120, 139)
GPS_LOSS = range(60, 100)
DETAIL_FRAME = 90
ACK_FRAME = 150
STALE_AFTER = timedelta(seconds=60)
ACTIVE_WINDOW = timedelta(minutes=15)
TRIP_GAP = timedelta(minutes=5)
MODEL_VERSION = "mock-catboost-1"
UNKNOWN_UNITS = (990001, 990002)
VEHICLES_TOTAL = 32

_WS = TypeAdapter(S.WsMessage)


def fmt(t: datetime) -> str:
    """Naive ISO-время контракта."""
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def frame_time(frame: int) -> datetime:
    return SIM_START + STEP * frame


def stop_key(geom: str) -> str:
    """Стабильный короткий ключ остановки по её координатам."""
    return "st_" + hashlib.md5(geom.encode()).hexdigest()[:8]


def short_name(address: str) -> str:
    """Адрес без номера дома — для названий маршрутов."""
    return address.split(", д.")[0].split(", вл.")[0]


def ws(kind: str, t: datetime, data: Any) -> dict:
    """WS-сообщение; тип проверяется дискриминированным union контракта."""
    msg = {"type": kind, "session_id": SESSION_ID, "sim_time": fmt(t), "data": data}
    return _WS.validate_python(msg).model_dump(mode="json")


def load_plan() -> pd.DataFrame:
    """Плановое расписание validate с ключами остановок и номерами рейсов."""
    s = pd.read_csv(RAW / "schedule_plan.csv")
    s["tb"] = pd.to_datetime(s["time_begin"])
    s["building_address"] = s["building_address"].fillna("Остановка без адреса")
    xy = s["geom"].str.extract(r"POINT \(([\d.]+) ([\d.]+)\)").astype(float)
    s["lon"], s["lat"] = xy[0], xy[1]
    s["stop_key"] = s["geom"].map(stop_key)
    s = s.sort_values(["tr_id", "tb"]).reset_index(drop=True)
    gap = s.groupby("tr_id")["tb"].diff()
    s["trip"] = (gap.isna() | (gap >= TRIP_GAP)).astype(int).groupby(s["tr_id"]).cumsum()
    return s


def load_tracks() -> dict[int, pd.DataFrame]:
    """Валидные точки телеметрии по ТС."""
    cols = ["tr_id", "unit_id", "event_time", "location_valid", "lon", "lat", "speed", "heading"]
    t = pd.read_csv(RAW / "traffic.csv", usecols=cols, low_memory=False)
    t = t[t["location_valid"] == True].copy()  # noqa: E712 — в CSV строки 'True'/'False'
    t["et"] = pd.to_datetime(t["event_time"], format="mixed").dt.floor("s")
    t = t.sort_values(["tr_id", "et"])
    return {int(k): g.reset_index(drop=True) for k, g in t.groupby("tr_id")}
