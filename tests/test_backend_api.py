"""REST и WebSocket бэкенда против контрактных моделей (TestClient, без фоновых задач)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from backend_kit import FakeWall, feed_rows, has_raw, make_runtime, ml_transport, real_traffic
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from services.backend.app.config import Settings
from services.backend.app.main import create_app
from services.backend.app.pipeline.scheduler import PipelineRunner
from services.backend.app.runtime import Runtime
from transit_core import schemas as S

START = datetime(2026, 1, 6, 7, 0, 0)
WS = TypeAdapter(S.WsMessage)
needs_data = pytest.mark.skipif(not has_raw(), reason="нет data/raw")


@pytest.fixture(scope="module")
def live():
    """Бэкенд после 30 сим-минут потока и проходов прогнозов (с красными прогнозами)."""
    if not has_raw():
        pytest.skip("нет data/raw")
    import asyncio

    wall = FakeWall()
    rt = make_runtime(wall, transport=ml_transport(delay_add=400.0))
    t_from = START - timedelta(minutes=20)
    client = TestClient(create_app(rt, background=False))
    with client:
        body = {"session_id": "s-test", "sim_time": t_from.isoformat(), "speed": 1.0,
                "state": "running", "warmup_until": None}  # fmt: skip
        assert client.post("/internal/sim/session", json=body).json()["new_session"]
        runner, tr = PipelineRunner(rt), real_traffic()
        for m in range(30):
            a, b = t_from + timedelta(minutes=m), t_from + timedelta(minutes=m + 1)
            feed_rows(rt, tr[(tr["et"] >= a) & (tr["et"] < b)].sort_values("et"), wall, t_from)
            wall.t = 1000.0 + (b - t_from).total_seconds()
            if (due := runner.due()) is not None:
                asyncio.run(runner.run_pass(due))
        yield client, rt


SINGLE = {
    "/api/v1/config": S.AppConfig,
    "/api/v1/network": S.Network,
    "/api/v1/metrics/summary": S.MetricsSummary,
    "/api/v1/metrics/quality": S.QualityMetrics,
    "/api/v1/ingest/stats": S.IngestStats,
    "/api/v1/sim/clock": S.SimClock,
    "/api/v1/health": S.Health,
}
LISTS = {
    "/api/v1/vehicles": S.VehicleState,
    "/api/v1/incidents": S.Incident,
    "/api/v1/segments/risk": S.SegmentRisk,
}


@needs_data
@pytest.mark.parametrize(("path", "model"), SINGLE.items())
def test_single_endpoints_match_contract(live, path, model):
    client, _ = live
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    model.model_validate(resp.json())


@needs_data
@pytest.mark.parametrize(("path", "model"), LISTS.items())
def test_list_endpoints_match_contract(live, path, model):
    client, _ = live
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    items = TypeAdapter(list[model]).validate_python(resp.json())
    assert items, f"{path} пуст"


@needs_data
def test_vehicle_detail_for_every_vehicle(live):
    client, _ = live
    kinds, rich = set(), 0
    for v in client.get("/api/v1/vehicles").json():
        resp = client.get(f"/api/v1/vehicles/{v['vehicle_id']}")
        assert resp.status_code == 200
        detail = S.VehicleDetail.model_validate(resp.json())
        kinds.add(detail.vehicle.kind)
        rich += bool(detail.timeline and detail.deviation_series and detail.forecast)
    assert {"scheduled", "no_schedule"} <= kinds
    assert rich >= 5, "у ТС с прогнозом есть нитка графика, ряд отклонения и прогноз"
    assert client.get("/api/v1/vehicles/nope").status_code == 404


@needs_data
def test_incident_filter_get_and_ack(live):
    client, _ = live
    incs = client.get("/api/v1/incidents", params={"status": "open,ack"}).json()
    assert incs and all(i["status"] in ("open", "ack") for i in incs)
    inc_id = incs[0]["id"]
    assert client.get(f"/api/v1/incidents/{inc_id}").json()["id"] == inc_id
    body = {"action_code": "DRIVER_CONTACT", "comment": "Связались"}
    acked = S.Incident.model_validate(
        client.post(f"/api/v1/incidents/{inc_id}/ack", json=body).json()
    )
    assert acked.status == "ack" and acked.ack.action_code == "DRIVER_CONTACT"
    assert client.post("/api/v1/incidents/nope/ack", json=body).status_code == 404
    assert client.get("/api/v1/incidents", params={"status": "bogus"}).status_code == 422


@needs_data
def test_prometheus_metrics_and_docs(live):
    client, _ = live
    assert "tp_ingest_to_state_ms" in client.get("/metrics").text
    assert client.get("/openapi.json").status_code == 200


@needs_data
def test_ws_first_message_is_snapshot_and_all_messages_follow_contract(live):
    client, rt = live
    with client.websocket_connect("/ws/v1/stream") as ws:
        first = WS.validate_python(ws.receive_json())
        assert first.type == "snapshot" and first.session_id == "s-test"
        hub = client.app.state.hub

        def emit_all() -> None:  # в event loop приложения: очереди asyncio не потокобезопасны
            hub.last_sent.clear()
            hub.flush_delta()
            hub.check_status(force=True)
            hub.broadcast(hub.message("kpis", rt.kpis()))
            hub.broadcast(hub.message("ping", None))

        client.portal.call(emit_all)
        kinds = [WS.validate_python(ws.receive_json()).type for _ in range(4)]
    assert kinds == ["vehicles.delta", "system.status", "kpis", "ping"]


@needs_data
def test_ws_snapshot_on_new_session_and_incident_events(live):
    client, rt = live
    with client.websocket_connect("/ws/v1/stream") as ws:
        ws.receive_json()
        inc = next(iter(rt.book.incidents.values()))
        client.post(f"/api/v1/incidents/{inc.id}/ack", json={"action_code": "MONITOR"})
        msg = WS.validate_python(ws.receive_json())
        assert msg.type == "incident.updated" and msg.data.id == inc.id


def test_health_fails_fast_without_data(tmp_path):
    rt = Runtime(Settings(data_dir=tmp_path, models_dir=tmp_path, ndtp_enabled=False))
    with TestClient(create_app(rt, background=False)) as client:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 503
        health = S.Health.model_validate(resp.json())
        assert health.status == "error" and "data_prep" in health.checks["data"]
        assert client.get("/api/v1/network").status_code == 503
        S.AppConfig.model_validate(client.get("/api/v1/config").json())
        with client.websocket_connect("/ws/v1/stream") as ws:
            snap = WS.validate_python(ws.receive_json())
            assert snap.type == "snapshot" and snap.data.status.mode == "OFFLINE"


def test_new_session_via_internal_api_sends_snapshot(tmp_path):
    rt = Runtime(Settings(data_dir=tmp_path, models_dir=tmp_path, ndtp_enabled=False))
    with TestClient(create_app(rt, background=False)) as client:
        with client.websocket_connect("/ws/v1/stream") as ws:
            ws.receive_json()
            body = {"session_id": "s-2", "sim_time": "2026-01-06T07:00:00", "speed": 30.0,
                    "state": "running"}  # fmt: skip
            client.post("/internal/sim/session", json=body)
            snap = WS.validate_python(ws.receive_json())
            assert snap.type == "snapshot" and snap.session_id == "s-2"
            status = WS.validate_python(ws.receive_json())
            assert status.type == "system.status" and status.data.mode == "LIVE"
        clock = S.SimClock.model_validate(client.get("/api/v1/sim/clock").json())
        assert clock.session_id == "s-2" and clock.speed == 30.0


def test_replay_control_reports_replayer_down(tmp_path):
    settings = Settings(data_dir=tmp_path, models_dir=tmp_path, ndtp_enabled=False,
                        replayer_url="http://127.0.0.1:9")  # fmt: skip
    with TestClient(create_app(Runtime(settings), background=False)) as client:
        resp = client.post("/api/v1/replay/control", json={"action": "pause"})
        assert resp.status_code == 502 and "Replayer" in resp.json()["detail"]
