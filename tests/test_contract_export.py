import json
from pathlib import Path

from transit_core import schemas
from transit_core.contract_export import build_json_schema, build_typescript

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


def test_committed_contract_is_up_to_date():
    """Если упал — перегенерируйте: uv run python -m transit_core.contract_export --out contracts"""
    schema = build_json_schema()
    committed_json = json.loads((CONTRACTS / "schema" / "contract.schema.json").read_text("utf-8"))
    committed_ts = (CONTRACTS / "ts" / "contract.ts").read_text("utf-8")

    assert committed_json == schema
    assert committed_ts == build_typescript(schema)


def test_typescript_exports_every_contract_model():
    ts = build_typescript()

    for model in schemas.REST_MODELS:
        assert f"export interface {model.__name__} " in ts
    assert "export type WsMessage = WsSnapshot | WsVehiclesDelta" in ts
    assert "export type RiskLevel = " in ts
    assert "risk_level: RiskLevel;" in ts
    assert "sim_time: NaiveTime;" in ts


def test_defaults_are_required_in_serialized_schema():
    config = build_json_schema()["$defs"]["AppConfig"]

    assert {"thresholds", "colors", "horizon_min", "schema_version"} <= set(config["required"])
