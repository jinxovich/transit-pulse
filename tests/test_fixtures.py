"""Закоммиченные моки дашборда обязаны соответствовать контракту."""

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from transit_core import schemas as S

FIXTURES = Path(__file__).resolve().parent.parent / "contracts" / "fixtures"
SINGLE = {
    "config.json": S.AppConfig,
    "health.json": S.Health,
    "network.json": S.Network,
    "metrics_summary.json": S.MetricsSummary,
    "metrics_quality.json": S.QualityMetrics,
    "ingest_stats.json": S.IngestStats,
    "sim_clock.json": S.SimClock,
}
LISTS = {
    "vehicles.json": S.VehicleState,
    "incidents.json": S.Incident,
    "segments_risk.json": S.SegmentRisk,
}


def _load(name: str):
    return json.loads((FIXTURES / name).read_text("utf-8"))


@pytest.mark.parametrize(("name", "model"), SINGLE.items())
def test_single_object_fixtures_match_contract(name, model):
    model.model_validate(_load(name))


@pytest.mark.parametrize(("name", "model"), LISTS.items())
def test_list_fixtures_match_contract(name, model):
    TypeAdapter(list[model]).validate_python(_load(name))


def test_snapshot_and_vehicle_details_match_contract():
    assert isinstance(
        TypeAdapter(S.WsMessage).validate_python(_load("snapshot.json")), S.WsSnapshot
    )
    details = list((FIXTURES / "vehicle_details").glob("*.json"))
    assert details
    for path in details:
        S.VehicleDetail.model_validate_json(path.read_text("utf-8"))


def test_ws_session_messages_match_contract_and_time_goes_forward():
    adapter = TypeAdapter(S.WsMessage)
    lines = (FIXTURES / "ws_session.jsonl").read_text("utf-8").splitlines()
    times = []
    for line in lines:
        frame = json.loads(line)
        times.append(frame["sim_time"])
        for msg in frame["messages"]:
            assert adapter.validate_python(msg).sim_time == frame["sim_time"]
    assert times == sorted(times)
