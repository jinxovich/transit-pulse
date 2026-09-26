"""ML-сервис: /predict через TestClient на маленькой обученной в фикстуре модели."""

import json

import numpy as np
import pandas as pd
import pytest
from catboost import CatBoostRegressor
from fastapi.testclient import TestClient

from services.ml.app.inference import p_exceed
from transit_core.features import FEATURES

N_TRAIN = 400


@pytest.fixture(scope="module")
def models_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("models")
    rng = np.random.default_rng(0)
    x = pd.DataFrame(rng.normal(size=(N_TRAIN, len(FEATURES))), columns=FEATURES)
    y = 40 * x["lead"] - 30 * x["spd5"] + rng.normal(scale=20, size=N_TRAIN)
    for mode in ("stream", "submission"):
        m = CatBoostRegressor(loss_function="MultiQuantile:alpha=0.1,0.5,0.9", iterations=60,
                              depth=3, verbose=False, allow_writing_files=False)
        m.fit(x, y)
        m.save_model(str(d / f"catboost_{mode}.cbm"))
    (d / "feature_list.json").write_text(json.dumps(FEATURES))
    metrics = {"stream": {"calibration": {"k": 1.5}}, "submission": {"calibration": {"k": 1.2}}}
    (d / "metrics.json").write_text(json.dumps(metrics))
    return d


@pytest.fixture(scope="module")
def client(models_dir):
    mp = pytest.MonkeyPatch()
    mp.setenv("MODELS_DIR", str(models_dir))
    from services.ml.app.serve import app

    with TestClient(app) as c:
        yield c
    mp.undo()


def _items(n: int, seed: int = 1) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        feats = {f: float(v) for f, v in zip(FEATURES, rng.normal(size=len(FEATURES)),
                                             strict=True)}
        feats["cur_dev"] = float(rng.uniform(-100, 300))
        feats["gps_dev"] = None
        feats.pop("dwell")
        out.append({"id": f"1:{i}", "features": feats})
    return out


def test_predict_contract(client):
    items = _items(30)
    r = client.post("/predict", json={"model": "stream", "items": items})

    assert r.status_code == 200
    body = r.json()
    assert body["model_version"] == "catboost-v2-stream"
    assert body["latency_ms"] >= 0
    assert [p["id"] for p in body["items"]] == [it["id"] for it in items]
    for p in body["items"]:
        assert p["q10"] <= p["delay_s"] <= p["q90"]
        assert 0.0 <= p["p_late"] <= 1.0
        assert p["expected_abs_error_s"] >= 0
        assert len(p["contributions"]) == 5
        mags = [abs(c["contribution_s"]) for c in p["contributions"]]
        assert mags == sorted(mags, reverse=True)


def test_default_model_is_stream_and_submission_available(client):
    r1 = client.post("/predict", json={"items": _items(2)})
    r2 = client.post("/predict", json={"model": "submission", "items": _items(2)})

    assert r1.json()["model_version"] == "catboost-v2-stream"
    assert r2.json()["model_version"] == "catboost-v2-submission"


def test_delay_shifts_with_cur_dev(client):
    item = _items(1)[0]
    low = {**item, "features": {**item["features"], "cur_dev": 0.0}}
    high = {**item, "features": {**item["features"], "cur_dev": 300.0}}

    r = client.post("/predict", json={"model": "submission", "items": [low, high]}).json()

    assert r["items"][1]["delay_s"] > r["items"][0]["delay_s"]
    assert r["items"][1]["p_late"] >= r["items"][0]["p_late"]


def test_empty_batch_and_bad_model(client):
    assert client.post("/predict", json={"items": []}).json()["items"] == []
    assert client.post("/predict", json={"model": "gru", "items": []}).status_code == 422


def test_health_info_metrics(client):
    h = client.get("/health").json()
    info = client.get("/model/info").json()
    client.post("/predict", json={"items": _items(3)})
    prom = client.get("/metrics").text

    assert h["status"] == "ok" and len(h["models"]) == 2
    assert info["features"] == FEATURES
    assert "ml_predict_latency_seconds_bucket" in prom
    assert "ml_predict_batch_size_count" in prom


def test_p_exceed_monotone_and_bounded():
    q = np.array([[-50.0, 20.0, 100.0], [100.0, 200.0, 400.0], [200.0, 300.0, 350.0],
                  [0.0, 0.0, 0.0]])

    p = p_exceed(q, 120.0)

    assert np.all((p >= 0) & (p <= 1))
    assert p[0] < p[1] < p[2]
    assert p[1] == pytest.approx(1 - (0.1 + 20 * 0.4 / 100))
