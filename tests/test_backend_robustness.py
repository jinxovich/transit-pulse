"""Устойчивость: обрыв всего потока, NaN от ML."""

from __future__ import annotations

import asyncio
from datetime import datetime

import httpx
import pytest
from backend_kit import FakeWall, has_raw, make_runtime, nav_frame, session

from services.backend.app.ingest.codec import FrameDecoder
from services.backend.app.ingest.worker import apply_frame
from services.backend.app.ml_client import MlClient

T0 = datetime(2026, 1, 6, 7, 0, 0)


@pytest.mark.skipif(not has_raw(), reason="нет data/raw")
def test_sweep_keeps_fleet_on_total_outage_but_drops_lone_silent_vehicle():
    wall = FakeWall()
    rt = make_runtime(wall)
    session(rt, "s1", T0, speed=60.0)
    dec = FrameDecoder()
    for unit in (663271, 664030):
        apply_frame(rt, wall(), dec.feed(nav_frame(unit, T0))[0])
    wall.advance(25.0)  # 25 с тишины на ×60 = 25 сим-минут, но молчит весь поток
    rt.sweep("LIVE")
    assert len(rt.store.vehicles) == 2
    apply_frame(rt, wall(), dec.feed(nav_frame(664030, rt.sim_now()))[0])
    rt.sweep("LIVE")
    assert set(rt.store.vehicles) == {"115106"}  # молчащий борт убран, живой остался


def test_ml_nan_item_falls_back_per_item():
    body = (
        '{"model_version": "m", "items": [{"id": "a", "delay_s": NaN, "p_late": 0.5},'
        ' {"id": "b", "delay_s": 50.0, "q10": 10.0, "q90": 90.0, "p_late": 0.2}]}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    client = MlClient("http://ml", transport=httpx.MockTransport(handler))
    out = asyncio.run(client.predict([("a", {}), ("b", {})]))
    assert set(out) == {"b"} and client.breaker.failures == 0
