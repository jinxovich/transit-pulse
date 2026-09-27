"""Дообучение без CV: ``train --refit`` берёт параметры последнего полного обучения."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from services.ml.app import train

SAVED = {
    "submission": {"best_synthetic_weight": 0.5, "final_iterations": 700, "keep": 1},
    "stream": {"best_synthetic_weight": 0.3, "final_iterations": 900},
    "ensemble": {"weights": {"catboost": 0.85}},
}


@pytest.fixture
def models(tmp_path, monkeypatch):
    (tmp_path / "metrics.json").write_text(json.dumps(SAVED))
    monkeypatch.setattr(train, "MODELS", tmp_path)
    meta = pd.DataFrame({"synthetic": [False, False, True]})
    monkeypatch.setattr(train, "labeled", lambda: SimpleNamespace(meta=meta))
    calls = []
    monkeypatch.setattr(train, "fit_final", lambda mode, table, w, it: calls.append((mode, w, it)))
    return tmp_path, calls


def test_refit_uses_saved_params_for_both_modes(models):
    _, calls = models

    assert train.main(["--refit"]) == 0

    assert calls == [("submission", 0.5, 700), ("stream", 0.3, 900)]


def test_refit_records_training_and_keeps_other_metrics(models):
    path, _ = models

    train.main(["--refit"])

    out = json.loads((path / "metrics.json").read_text())
    last = out["last_training"]
    assert last["kind"] == "refit"
    assert (last["n_real_points"], last["n_synthetic_points"]) == (2, 1)
    assert out["submission"] == SAVED["submission"] and out["ensemble"] == SAVED["ensemble"]


def test_refit_without_full_training_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(train, "MODELS", tmp_path)

    with pytest.raises(SystemExit, match="полного обучения"):
        train.main(["--refit"])
