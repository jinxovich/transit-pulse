import pandas as pd

from contracts.mockgen.common import NO_ADDRESS as MOCK_NO_ADDRESS
from contracts.mockgen.common import stop_key as mock_stop_key
from tests.conftest import require_raw, synthetic_schedule_csv
from transit_core.plan import NO_ADDRESS, PLAN_COLUMNS, load_plan, name_unaddressed


def test_load_plan_columns_and_order(tmp_path):
    plan = load_plan(synthetic_schedule_csv(tmp_path / "s.csv"))

    assert list(plan.columns) == PLAN_COLUMNS
    assert plan["tb"].is_monotonic_increasing
    assert plan["visit_id"].tolist() == list(range(1000, 1010))
    assert plan["new_trip"].tolist() == [1] + [0] * 9
    assert plan["trip"].nunique() == 1
    assert pd.isna(plan["gap_min"].iat[0]) and plan["gap_min"].iat[1] == 1.0


def test_load_plan_never_returns_fact(tmp_path):
    plan = load_plan(synthetic_schedule_csv(tmp_path / "s.csv", with_fact=True))

    assert "time_fact_begin" not in plan.columns
    assert not any("fact" in c for c in plan.columns)


def test_stop_key_and_name_match_contract_mocks(tmp_path):
    path = synthetic_schedule_csv(tmp_path / "s.csv")
    raw = pd.read_csv(path).sort_values("time_begin")
    plan = load_plan(path)

    assert plan["stop_key"].tolist() == raw["geom"].map(mock_stop_key).tolist()
    assert NO_ADDRESS == MOCK_NO_ADDRESS
    assert plan["name"].iat[2] == NO_ADDRESS


def test_trip_split_on_real_validate_plan():
    plan = load_plan(require_raw("validate", "schedule_plan.csv"))

    assert plan["tr_id"].nunique() == 13
    first = plan.groupby("tr_id").head(1)
    assert (first["new_trip"] == 1).all()
    assert ((plan["gap_min"] >= 5) == (plan["new_trip"] == 1) & plan["gap_min"].notna()).all()


def test_unaddressed_stop_named_by_position_on_trip(tmp_path):
    plan = load_plan(synthetic_schedule_csv(tmp_path / "s.csv"))

    shown = name_unaddressed(plan)

    assert shown["name"].iat[2] == "Остановка № 3 рейса"
    assert shown["name"].drop(index=2).tolist() == plan["name"].drop(index=2).tolist()
    assert plan["name"].iat[2] == NO_ADDRESS  # исходный план не меняется
