"""ML-сервис: /predict через TestClient на маленькой обученной в фикстуре модели."""

import json
import shutil
from pathlib import Path

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


def test_explain_false_skips_contributions_but_keeps_prediction(client):
    items = _items(4)
    full = client.post("/predict", json={"items": items}).json()["items"]
    fast = client.post("/predict", json={"items": items, "explain": False}).json()["items"]

    assert all(p["contributions"] == [] for p in fast)
    assert [p["delay_s"] for p in fast] == [p["delay_s"] for p in full]


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


CAL = {"version": 1, "global_scale": 2.0, "err_k": 0.5,
       "lead_bins": [{"lo": 10.0, "hi": 11.0, "scale": 1.5, "n": 900},
                     {"lo": 11.0, "hi": 15.0, "scale": 4.0, "n": 500}],
       "isotonic": {"x": [0.0, 0.3, 0.6, 1.0], "y": [0.02, 0.1, 0.7, 0.9]},
       "quality": {"brier_before": 0.13, "brier_after": 0.12}}


@pytest.fixture
def cal_client(models_dir, tmp_path):
    for f in models_dir.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    (tmp_path / "calibration_stream.json").write_text(json.dumps({"mode": "stream", **CAL}))
    mp = pytest.MonkeyPatch()
    mp.setenv("MODELS_DIR", str(tmp_path))
    from services.ml.app.serve import app

    with TestClient(app) as c:
        yield c
    mp.undo()


def _with_lead(item: dict, lead: float) -> dict:
    return {**item, "features": {**item["features"], "lead": lead}}


def test_predict_without_calibration_file_keeps_old_formula(models_dir):
    from services.ml.app.inference import load_registry, predict, to_matrix, widen

    reg = load_registry(models_dir)
    lm = reg.models["stream"]
    x = to_matrix([it["features"] for it in _items(5)], reg.features)
    base = np.nan_to_num(x[:, reg.features.index("cur_dev")], nan=0.0)
    q = np.sort(lm.model.predict(x), axis=1)
    q_abs = widen(q, lm.interval_scale) + base[:, None]

    out = predict(lm, x, reg.features, with_contributions=False)

    assert lm.calibration is None
    assert [p["p_late"] for p in out] == pytest.approx(np.round(p_exceed(q_abs), 4).tolist())
    assert [p["expected_abs_error_s"] for p in out] == pytest.approx(
        np.round(1.5 * (q[:, 2] - q[:, 0]) / 2, 2).tolist(), abs=0.011)


def test_predict_with_calibration_scales_interval_by_lead(cal_client):
    item = _items(1)[0]
    near, far = _with_lead(item, 11.0), _with_lead(item, 14.0)
    body = {"items": [near, far], "explain": False}

    cal = cal_client.post("/predict", json={**body, "model": "stream"}).json()["items"]
    old = cal_client.post("/predict", json={**body, "model": "submission"}).json()["items"]

    for p in cal:
        assert p["q10"] <= p["delay_s"] <= p["q90"]
        assert 0.02 <= p["p_late"] <= 0.9  # границы изотонических узлов
        assert p["expected_abs_error_s"] == pytest.approx(0.5 * (p["q90"] - p["q10"]) / 2,
                                                          abs=0.02)
    # у submission файла калибровки нет — прежний общий масштаб и p_late без изотоники
    for p in old:
        assert 0.0 <= p["p_late"] <= 1.0


def test_calibrated_width_follows_lead_bin_scale(models_dir, tmp_path):
    from services.ml.app.inference import load_registry, predict, to_matrix

    (tmp_path / "calibration_stream.json").write_text(json.dumps(CAL))
    for f in models_dir.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    reg = load_registry(tmp_path)
    lm = reg.models["stream"]
    item = _items(1)[0]["features"]
    x = to_matrix([{**item, "lead": 11.0}, {**item, "lead": 14.0}, {**item, "lead": None}],
                  reg.features)
    # одна и та же сырая ширина → отличие только в масштабе корзины
    q = np.sort(lm.model.predict(x), axis=1)
    raw = q[:, 2] - q[:, 0]

    out = predict(lm, x, reg.features, with_contributions=False)

    widths = np.array([p["q90"] - p["q10"] for p in out])
    assert widths == pytest.approx(raw * np.array([1.5, 4.0, 2.0]), abs=0.02)


def test_model_info_reports_calibration(cal_client):
    info = cal_client.get("/model/info").json()

    assert info["calibration"]["stream"]["version"] == 1
    assert info["calibration"]["stream"]["lead_bins"][1]["scale"] == 4.0
    assert info["calibration"]["stream"]["brier_after"] == 0.12
    assert info["calibration"]["submission"] is None


def test_broken_or_missing_calibration_file_means_old_behaviour(tmp_path):
    from services.ml.app.inference import load_calibration

    bad = tmp_path / "calibration_stream.json"
    bad.write_text('{"version": 1}')

    assert load_calibration(bad) is None
    assert load_calibration(tmp_path / "missing.json") is None


REPO_MODELS = Path(__file__).resolve().parents[1] / "models"


@pytest.mark.skipif(not (REPO_MODELS / "catboost_stream.cbm").is_file(), reason="нет models/")
def test_swagger_examples_predict_ok_on_repo_models(monkeypatch):
    from services.ml.app.openapi_examples import PREDICT_EXAMPLES, VEHICLE_122048
    from services.ml.app.serve import app

    monkeypatch.setenv("MODELS_DIR", str(REPO_MODELS))
    with TestClient(app) as c:
        body = c.get("/openapi.json").json()["paths"]["/predict"]["post"]["requestBody"]
        examples = body["content"]["application/json"]["examples"]
        assert set(examples) == set(PREDICT_EXAMPLES)
        assert list(VEHICLE_122048) == FEATURES
        for ex in PREDICT_EXAMPLES.values():
            items = ex["value"]["items"]
            assert all(set(it["features"]) <= set(FEATURES) for it in items)
            r = c.post("/predict", json=ex["value"])
            assert r.status_code == 200, r.text
            assert [p["id"] for p in r.json()["items"]] == [it["id"] for it in items]


def _retrained_copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    rng = np.random.default_rng(7)
    x = pd.DataFrame(rng.normal(size=(N_TRAIN, len(FEATURES))), columns=FEATURES)
    m = CatBoostRegressor(loss_function="MultiQuantile:alpha=0.1,0.5,0.9", iterations=30,
                          depth=2, verbose=False, allow_writing_files=False)
    m.fit(x, 10 * x["lead"])
    m.save_model(str(dst / "catboost_stream.cbm"))
    return dst


@pytest.fixture
def reload_client(models_dir, tmp_path, monkeypatch):
    live = tmp_path / "live"
    shutil.copytree(models_dir, live)
    monkeypatch.setenv("MODELS_DIR", str(live))
    from services.ml.app.serve import app

    with TestClient(app) as c:
        yield c, live


def test_reload_same_files_is_unchanged(reload_client):
    client, _ = reload_client

    r = client.post("/model/reload")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unchanged" and body["before"] == body["after"]
    assert set(body["after"]) == {"stream", "submission"}


def test_reload_picks_up_retrained_model(reload_client, tmp_path, monkeypatch):
    client, live = reload_client
    before = client.get("/model/info").json()["fingerprints"]
    monkeypatch.setenv("MODELS_DIR", str(_retrained_copy(live, tmp_path / "retrained")))

    body = client.post("/model/reload").json()

    assert body["status"] == "reloaded"
    assert body["after"]["stream"] != before["stream"]
    assert body["after"]["submission"] == before["submission"]
    assert client.get("/model/info").json()["fingerprints"] == body["after"]


def test_failed_reload_keeps_serving_old_models(reload_client, tmp_path, monkeypatch):
    client, _ = reload_client
    before = client.get("/model/info").json()["fingerprints"]
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("MODELS_DIR", str(empty))

    r = client.post("/model/reload")

    assert r.status_code == 500 and "прежние" in r.json()["detail"]
    assert client.get("/model/info").json()["fingerprints"] == before
    ok = client.post("/predict", json={"model": "stream", "items": _items(3), "explain": False})
    assert ok.status_code == 200 and len(ok.json()["items"]) == 3
