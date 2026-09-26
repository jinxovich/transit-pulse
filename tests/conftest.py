"""Общие фикстуры: синтетический маршрут и трек с известной задержкой."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from transit_core.track import M_PER_DEG_LON

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
LON0, LAT0 = 37.6, 55.75
START = datetime(2026, 1, 6, 8, 0, 0)
STOP_STEP_M = 400.0
STOP_STEP_S = 60
N_STOPS = 10
TRUE_DELAY_S = 30.0
DWELL0_S = 120
TRACK_STEP_S = 10


def _lonlat(x_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return LON0 + np.asarray(x_m) / M_PER_DEG_LON, np.full(np.shape(x_m), LAT0)


def synthetic_schedule_csv(path: Path, with_fact: bool = True) -> Path:
    """Расписание одного ТС: остановки по прямой через 400 м, план через 60 с."""
    x = np.arange(N_STOPS) * STOP_STEP_M
    lon, lat = _lonlat(x)
    rows = []
    for k in range(N_STOPS):
        tb = START + timedelta(seconds=STOP_STEP_S * k)
        rows.append(
            {
                "tt_action_item_id": 1000 + k,
                "time_begin": tb.strftime("%Y-%m-%d %H:%M:%S"),
                "time_fact_begin": (tb + timedelta(seconds=TRUE_DELAY_S)).isoformat(sep=" "),
                "order_date": "2026-01-06",
                "manual_fill": k % 3 == 0,
                "tr_id": 7,
                "geom": f"POINT ({lon[k]:.8f} {lat[k]:.8f})",
                "building_address": None if k == 2 else f"ул. Тестовая, д.{k}",
            }
        )
    df = pd.DataFrame(rows)
    if not with_fact:
        df = df.drop(columns="time_fact_begin")
    df.sample(frac=1.0, random_state=0).to_csv(path, index=False)
    return path


def synthetic_track() -> pd.DataFrame:
    """Трек: стоит на первой остановке до tb0+30 с, дальше едет 24 км/ч (опоздание 30 с)."""
    v = STOP_STEP_M / STOP_STEP_S
    t_rel = np.arange(-DWELL0_S, STOP_STEP_S * N_STOPS, TRACK_STEP_S, dtype=float)
    x = np.clip((t_rel - TRUE_DELAY_S) * v, 0.0, None)
    lon, lat = _lonlat(x)
    et = pd.Timestamp(START) + pd.to_timedelta(t_rel, unit="s")
    speed = np.where(t_rel > TRUE_DELAY_S, v * 3.6, 0.0)
    return pd.DataFrame(
        {
            "et": et.to_numpy(dtype="datetime64[ns]"),
            "lon": lon,
            "lat": lat,
            "speed": np.trunc(speed),
            "heading": np.full(len(x), 90.0),
            "valid": np.ones(len(x), dtype=bool),
        }
    )


@pytest.fixture
def synth_plan(tmp_path):
    from transit_core.plan import load_plan

    return load_plan(synthetic_schedule_csv(tmp_path / "schedule.csv"))


@pytest.fixture
def synth_track():
    return synthetic_track()


def require_raw(*parts: str) -> Path:
    """Путь к файлу датасета; тест пропускается, если данных нет."""
    path = RAW.joinpath(*parts)
    if not path.exists():
        pytest.skip(f"нет данных {path}")
    return path
