"""Анти-утечка на уровне данных и кода обучения."""

import ast
from pathlib import Path

import pandas as pd

from tests.conftest import require_raw

ROOT = Path(__file__).resolve().parents[1]
# Модули, которые строят признаки/обучают/предсказывают: факт в них упоминаться не должен.
# plan.py упоминает колонку только в защитной проверке (см. tests/test_plan.py).
NO_FACT_MODULES = [
    "packages/transit_core/transit_core/features.py",
    "packages/transit_core/transit_core/stops_detector.py",
    "packages/transit_core/transit_core/track.py",
    "services/ml/app/dataset.py",
    "services/ml/app/train.py",
    "services/ml/app/serve.py",
    "services/ml/app/inference.py",
    "scripts/make_submission.py",
]


def test_validate_targets_never_in_training_labels():
    val = pd.read_csv(require_raw("validate", "points.csv"))
    train = pd.concat([pd.read_csv(require_raw("labels", f)) for f in
                       ("labels_train.csv", "labels_test.csv")])

    assert not set(val["target_stop_id"]) & set(train["target_stop_id"])
    assert not set(val["sample_id"]) & set(train["sample_id"])


def test_feature_and_training_code_never_mentions_fact_column():
    for rel in NO_FACT_MODULES:
        tree = ast.parse((ROOT / rel).read_text("utf-8"))
        strings = [n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        code = [s for s in strings if "time_fact_begin" in s and " " not in s]
        assert not code, f"{rel} обращается к time_fact_begin"


def test_validate_dataset_uses_plan_without_fact():
    from services.ml.app.dataset import SPLITS

    plan_file = SPLITS["validate"][0]
    assert plan_file == "validate/schedule_plan.csv"
    header = require_raw("validate", "schedule_plan.csv").open(encoding="utf-8").readline()
    assert "time_fact_begin" not in header
