"""HTTP-обёртка replayer: /health, /status, /control."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from replayer.main import app

CSV = (
    "unit_id,event_time,location_valid,lon,lat,alt,speed,heading\n"
    "101,2026-01-06 06:59:30,True,37.60,55.70,150,20,90\n"
    "101,2026-01-06 07:00:30,False,,,,,\n"
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    (tmp_path / "validate").mkdir()
    (tmp_path / "validate" / "traffic.csv").write_text(CSV)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BACKEND_NDTP", "127.0.0.1:1")
    monkeypatch.setenv("BACKEND_HTTP", "http://127.0.0.1:1")
    monkeypatch.setenv("REPLAY_AUTOSTART", "0")
    with TestClient(app) as c:
        yield c


def test_health_and_status_before_start(client):
    assert client.get("/health").json()["status"] == "ok"
    st = client.get("/status").json()
    assert st["state"] == "stopped" and st["units"] == 1 and st["packets_sent"] == 0
    assert st["sim_time"] == "2026-01-06T07:00:00"


def test_control_flow(client):
    st = client.post("/control", json={"action": "start"}).json()
    assert st["state"] == "running" and st["session_id"].startswith("s-")
    assert st["warmup_until"] == "2026-01-06T07:00:00"
    assert client.post("/control", json={"action": "speed", "speed": 60}).json()["speed"] == 60
    assert client.post("/control", json={"action": "pause"}).json()["state"] == "paused"
    assert client.post("/control", json={"action": "resume"}).json()["state"] == "running"
    seek = {"action": "seek", "seek_to": "2026-01-06T12:00:00"}
    assert client.post("/control", json=seek).json()["session_id"] != st["session_id"]


def test_control_rejects_bad_input(client):
    assert client.post("/control", json={"action": "seek"}).status_code == 422
    assert client.post("/control", json={"action": "speed", "speed": -1}).status_code == 422
    assert client.post("/control", json={"action": "jump"}).status_code == 422
