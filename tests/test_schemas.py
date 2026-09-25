import pytest
from pydantic import TypeAdapter, ValidationError

from transit_core.schemas import (
    AppConfig,
    Kpis,
    RiskCounts,
    SimClock,
    WsKpis,
    WsMessage,
    WsPing,
)

WS = TypeAdapter(WsMessage)
KPIS = {
    "vehicles_online": 12,
    "vehicles_total": 30,
    "by_risk": {"green": 8, "yellow": 2, "red": 1, "early": 1, "none": 18},
    "incidents_open": 1,
    "avg_predicted_delay_s": 42.0,
    "lead_ok_share": 1.0,
    "e2e_latency_ms_p95": 120.0,
    "ml_latency_ms_p95": 8.0,
    "ingest_pps": 3.5,
}


def test_ws_message_is_dispatched_by_type():
    raw = {"type": "kpis", "session_id": "s1", "sim_time": "2026-01-06T07:00:00", "data": KPIS}

    msg = WS.validate_python(raw)

    assert isinstance(msg, WsKpis)
    assert msg.data.by_risk.red == 1
    assert msg.schema_version == "1.0"


def test_ws_incident_accepts_all_three_event_types():
    for kind in ("incident.opened", "incident.updated", "incident.resolved"):
        with pytest.raises(ValidationError) as err:
            WS.validate_python({"type": kind, "session_id": "s", "sim_time": "2026-01-06T07:00:00"})
        assert "data" in str(err.value)


def test_naive_time_rejects_timezone():
    with pytest.raises(ValidationError):
        SimClock(sim_time="2026-01-06T07:00:00+03:00", session_id="s", speed=1, state="running")


def test_models_are_immutable_and_strict():
    counts = RiskCounts(green=1, yellow=0, red=0, early=0, none=0)
    with pytest.raises(ValidationError):
        counts.green = 2
    with pytest.raises(ValidationError):
        Kpis(**KPIS, unexpected=1)


def test_config_defaults_match_organizer_late_threshold():
    cfg = AppConfig(sim_speed=30, session_id="s")

    assert cfg.thresholds.red_delay_s == 120
    assert cfg.horizon_min == (10, 15)


def test_ping_roundtrip_json():
    msg = WsPing(session_id="s", sim_time="2026-01-06T07:00:00")

    assert WS.validate_json(msg.model_dump_json()) == msg
