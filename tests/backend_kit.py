"""Общие помощники тестов бэкенда: подменные часы, ML, данные, NDTP-кадры."""

from __future__ import annotations

import json
import math
from datetime import datetime
from functools import cache
from pathlib import Path

import httpx
import pandas as pd

from services.backend.app.config import Settings
from services.backend.app.ingest.codec import (
    FrameDecoder,
    NavCell,
    encode_handshake,
    encode_realtime,
)
from services.backend.app.ingest.worker import apply_frame
from services.backend.app.runtime import Runtime
from services.backend.app.state.clock import SessionUpdate
from services.backend.app.state.static import StaticData, load_static
from services.backend.app.state.timefmt import to_epoch

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
DEAD_ML = "http://127.0.0.1:9"


class FakeWall:
    """Настенные часы, которые двигает тест."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def has_raw() -> bool:
    return (RAW / "validate" / "schedule_plan.csv").is_file()


@cache
def real_static() -> StaticData:
    """Статика validate (грузится один раз на весь прогон тестов)."""
    return load_static(RAW)


@cache
def real_traffic() -> pd.DataFrame:
    from transit_core.track import load_traffic

    return load_traffic(RAW / "validate" / "traffic.csv")


def settings(tmp: Path | None = None, **kw) -> Settings:
    base = {"data_dir": RAW, "ml_url": DEAD_ML, "ndtp_enabled": False, "db_path": Path(":memory:")}
    if tmp is not None:
        base["models_dir"] = tmp
    return Settings(**{**base, **kw})


def make_runtime(wall: FakeWall | None = None, transport=None, static=None, **kw) -> Runtime:
    """Runtime на данных validate с подменными часами и (опц.) поддельным ml."""
    return Runtime(
        settings(**kw), static=static or real_static(), wall=wall or FakeWall(),
        ml_transport=transport,
    )  # fmt: skip


def session(rt: Runtime, sid: str, sim: datetime, speed: float = 1.0, state="running") -> bool:
    return rt.apply_session(SessionUpdate(sid, sim, speed, state))


def nav_frame(unit_id: int, et: datetime, lon=37.6, lat=55.75, valid=True, speed=20) -> bytes:
    nav = NavCell(to_epoch(et), lon, lat, valid, speed, speed, 90)
    return encode_realtime(unit_id, 1, nav)


def handshake(unit_id: int) -> bytes:
    return encode_handshake(unit_id, 1)


def feed_rows(rt: Runtime, rows: pd.DataFrame, wall: FakeWall, t_from: datetime) -> None:
    """Проигрывает строки телеметрии (как после NDTP) напрямую в state, двигая часы."""
    dec = FrameDecoder()
    for r in rows.itertuples():
        wall.t = 1000.0 + (r.et - t_from).total_seconds() / rt.clock.speed
        raw = nav_frame(int(r.unit_id), r.et.to_pydatetime(), r.lon, r.lat, bool(r.valid),
                        int(r.speed))  # fmt: skip
        frame = dec.feed(raw)[0]
        rt.stats.on_frame(frame, wall.t)
        apply_frame(rt, wall.t, frame)


def ml_transport(delay_add: float = 0.0, fail: bool = False, calls: list | None = None):
    """Поддельный ML-сервис INTERFACES §4: ``delay = cur_dev + delay_add``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        if fail:
            return httpx.Response(500, json={"detail": "boom"})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "model_version": "test-ml"})
        body = json.loads(request.content)
        items = []
        for it in body["items"]:
            cur = it["features"].get("cur_dev")
            d = (0.0 if cur is None or math.isnan(cur) else cur) + delay_add
            items.append(
                {
                    "id": it["id"],
                    "delay_s": d,
                    "q10": d - 40,
                    "q90": d + 40,
                    "p_late": 0.9 if d > 120 else 0.1,
                    "expected_abs_error_s": 45.0,
                    "contributions": [{"feature": "cur_dev", "contribution_s": 30.0}],
                }
            )
        return httpx.Response(200, json={"model_version": "test-ml", "latency_ms": 1.0,
                                         "items": items})  # fmt: skip

    return httpx.MockTransport(handler)
