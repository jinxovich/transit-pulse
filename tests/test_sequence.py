"""Тесты последовательностей GRU: форма, анти-утечка, детерминизм, паритет ONNX."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from transit_core.sequence import (
    N_SEQ_FEATURES,
    N_STATIC_FEATURES,
    N_STEPS,
    build_sequence,
    prepare_plan,
    sequence_static,
)

START = datetime(2026, 1, 6, 8, 0, 0)
T = START + timedelta(minutes=30)
LON0, LAT0 = 37.6, 55.75
ONNX_PATH = Path(__file__).resolve().parents[1] / "models" / "gru.onnx"


def _track(minutes: int = 60, step_s: int = 12) -> pd.DataFrame:
    """ТС едет на восток 30 км/ч с остановкой на 10-й минуте, каждая 7-я точка невалидна."""
    n = minutes * 60 // step_s
    et = [START + timedelta(seconds=step_s * i) for i in range(n)]
    speed = np.where((np.arange(n) * step_s // 60) == 10, 0.0, 30.0)
    x_m = np.cumsum(speed / 3.6 * step_s)
    valid = np.arange(n) % 7 != 3
    df = pd.DataFrame({
        "et": et,
        "lon": np.round(LON0 + x_m / 62_600, 7),
        "lat": LAT0,
        "speed": speed,
        "heading": 90,
        "valid": valid,
    })
    df.loc[~valid, ["lon", "lat", "speed", "heading"]] = np.nan
    return df


def _plan() -> pd.DataFrame:
    rows = []
    for k in range(40):
        lon = LON0 + k * 400 / 62_600
        rows.append({
            "tt_action_item_id": 1000 + k,
            "tr_id": 1,
            "time_begin": (START + timedelta(minutes=2 * k + (20 if k >= 25 else 0))).isoformat(),
            "manual_fill": "True" if k % 2 else "False",
            "geom": f"POINT ({lon:.8f} {LAT0:.8f})",
            "time_fact_begin": "must-not-be-read",
        })
    return prepare_plan(pd.DataFrame(rows))


def test_shape_and_dtype():
    seq = build_sequence(_track(), T, _plan())
    assert seq.shape == (N_STEPS, N_SEQ_FEATURES) == (80, 8)
    assert seq.dtype == np.float32
    assert np.isfinite(seq).all()


def test_flags_reflect_data():
    seq = build_sequence(_track(), T)
    assert seq[:, 3].sum() == 0  # при шаге 12 с каждый 15-секундный шаг не пуст
    assert 0 < seq[:, 2].mean() < 1  # есть невалидные строки
    assert seq[:, 6:].sum() == 0  # без плана плановые признаки нулевые


def test_empty_track_is_all_no_data():
    seq = build_sequence(_track().iloc[:0], T, _plan())
    assert (seq[:, 3] == 1).all()
    assert np.abs(seq[:, [0, 1, 2, 4, 5, 6, 7]]).sum() == 0


def test_no_leak_after_t():
    track, plan = _track(), _plan()
    base = build_sequence(track, T, plan)
    spoiled = track.copy()
    after = spoiled["et"] > T
    spoiled.loc[after, ["lon", "lat"]] = 0.0
    spoiled.loc[after, "speed"] = 999.0
    spoiled.loc[after, "valid"] = ~spoiled.loc[after, "valid"]
    np.testing.assert_array_equal(base, build_sequence(spoiled, T, plan))
    s1 = sequence_static(T, 1020, T + timedelta(minutes=12), 60.0, plan, track)
    s2 = sequence_static(T, 1020, T + timedelta(minutes=12), 60.0, plan, spoiled)
    np.testing.assert_array_equal(s1, s2)


def test_deterministic_and_order_invariant():
    track, plan = _track(), _plan()
    a = build_sequence(track, T, plan)
    shuffled = track.sample(frac=1.0, random_state=0)
    np.testing.assert_array_equal(a, build_sequence(shuffled, T, plan))
    np.testing.assert_array_equal(a, build_sequence(track, T, plan))


def test_motion_and_stop_encoded():
    seq = build_sequence(_track(), START + timedelta(minutes=12), None)
    stop_steps = seq[:, 1] == 1
    assert stop_steps.any() and not stop_steps.all()
    assert (seq[~stop_steps & (seq[:, 2] > 0), 4] > 0).any()  # движение на восток: dx > 0
    assert np.abs(seq[:, 5]).max() < 1e-6


def test_static_features():
    plan = _plan()
    target = int(plan.loc[25, "tt_action_item_id"])  # после 20-минутного разрыва
    f = sequence_static(T, target, plan.loc[25, "tb"], 120.0, plan, _track())
    assert f.shape == (N_STATIC_FEATURES,)
    assert f[0] == pytest.approx(120.0 / 300)
    assert f[5] == 1.0  # цель первая в рейсе
    assert f[8] == 1.0  # разрыв между T и целью


def test_prepare_plan_ignores_fact():
    assert "time_fact_begin" not in _plan().columns


@pytest.mark.skipif(not ONNX_PATH.exists(), reason="нет models/gru.onnx")
def test_onnx_output_finite_and_batch_dynamic():
    ort = pytest.importorskip("onnxruntime")
    sess = ort.InferenceSession(str(ONNX_PATH), providers=["CPUExecutionProvider"])
    plan, track = _plan(), _track()
    for b in (1, 7):
        seq = np.stack([build_sequence(track, T, plan)] * b)
        st = np.stack([sequence_static(T, 1020, T + timedelta(minutes=12), 60.0, plan, track)] * b)
        out = sess.run(None, {"seq": seq, "static": st})[0]
        assert out.shape == (b,) and np.isfinite(out).all()


def test_onnx_parity_with_torch(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from services.ml.app import seq_onnx
    from services.ml.app.seq_model import SeqRegressor

    rng = np.random.default_rng(0)
    seq = rng.normal(size=(5, N_STEPS, N_SEQ_FEATURES)).astype(np.float32)
    st = rng.normal(size=(5, N_STATIC_FEATURES)).astype(np.float32)
    models = [SeqRegressor(N_SEQ_FEATURES, N_STATIC_FEATURES).eval() for _ in range(2)]
    path = tmp_path / "gru.onnx"
    ens = seq_onnx.export(models, (seq, st), path)
    assert seq_onnx.parity(ens, seq_onnx.session(path), seq, st) < 1e-3
