"""E2E-смоук: бэкенд в процессе, 40 сим-минут validate/traffic.csv по TCP NDTP.

Как replayer: POST сессии, на каждый борт своё TCP-соединение с handshake, кадры
Nav00 по сим-времени на высокой скорости. Ожидаем прогнозы у ТС с расписанием.
"""

from __future__ import annotations

import socket
import time
from datetime import datetime, timedelta

import pytest
from backend_kit import handshake, has_raw, nav_frame, real_static, real_traffic, settings
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from services.backend.app.main import create_app
from services.backend.app.runtime import Runtime
from transit_core import schemas as S

START = datetime(2026, 1, 6, 6, 40, 0)
MINUTES = 40
SPEED = 400.0
TICK_S = 0.02


class Fleet:
    """TCP-соединения бортов к NDTP-порту бэкенда."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.socks: dict[int, socket.socket] = {}

    def send(self, unit_id: int, data: bytes) -> None:
        sock = self.socks.get(unit_id)
        if sock is None:
            sock = socket.create_connection(("127.0.0.1", self.port))
            sock.setblocking(True)
            sock.sendall(handshake(unit_id))
            self.socks[unit_id] = sock
        sock.sendall(data)

    def close(self) -> None:
        for sock in self.socks.values():
            sock.close()


def _replay(fleet: Fleet, rows, t0: float) -> int:
    sent, i, n = 0, 0, len(rows)
    et, units = rows["et"].to_list(), rows.itertuples()
    while i < n:
        sim_now = START + timedelta(seconds=(time.monotonic() - t0) * SPEED)
        while i < n and et[i] <= sim_now:
            r = next(units)
            raw = nav_frame(int(r.unit_id), r.et.to_pydatetime(), r.lon, r.lat, bool(r.valid),
                            int(r.speed))  # fmt: skip
            fleet.send(int(r.unit_id), raw)
            i, sent = i + 1, sent + 1
        time.sleep(TICK_S)
    return sent


@pytest.mark.skipif(not has_raw(), reason="нет data/raw")
def test_stream_of_40_sim_minutes_produces_predictions():
    rt = Runtime(settings(ndtp_enabled=True, ndtp_port=0, ndtp_host="127.0.0.1"),
                 static=real_static())  # fmt: skip
    tr = real_traffic()
    rows = tr[(tr["et"] >= START) & (tr["et"] < START + timedelta(minutes=MINUTES))]
    rows = rows.sort_values("et", kind="stable").reset_index(drop=True)
    with TestClient(create_app(rt)) as client:
        body = {"session_id": "e2e-1", "sim_time": START.isoformat(), "speed": SPEED,
                "state": "running"}  # fmt: skip
        client.post("/internal/sim/session", json=body)
        fleet = Fleet(client.app.state.ndtp_port)
        try:
            sent = _replay(fleet, rows, time.monotonic())
            time.sleep(1.5)  # последний проход прогнозов и дельта
        finally:
            fleet.close()
        stats = S.IngestStats.model_validate(client.get("/api/v1/ingest/stats").json())
        vehicles = TypeAdapter(list[S.VehicleState]).validate_python(
            client.get("/api/v1/vehicles").json()
        )
        summary = S.MetricsSummary.model_validate(client.get("/api/v1/metrics/summary").json())
    assert stats.packets_total >= sent and stats.crc_errors == 0
    assert summary.dropped_packets == 0
    predicted = [v for v in vehicles if v.kind == "scheduled" and v.prediction is not None]
    assert len(predicted) >= 5, [(v.vehicle_id, v.warming_up) for v in vehicles]
    for v in predicted:
        p = v.prediction
        assert not p.horizon_ok or 10 < p.lead_min <= 15
    assert summary.ingest_to_state.p95_ms is not None
