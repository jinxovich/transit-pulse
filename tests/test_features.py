import inspect
import time
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from tests.conftest import START, require_raw
from transit_core.features import FEATURES, point_features
from transit_core.plan import load_plan, split_by_tr
from transit_core.track import load_traffic, split_tracks


def _corrupt_future(track: pd.DataFrame, t, seed: int = 0) -> pd.DataFrame:
    """Портит все строки трека с et > t (время не трогаем — порядок сохраняется)."""
    rng = np.random.default_rng(seed)
    out = track.copy()
    fut = out["et"] > np.datetime64(t)
    n = int(fut.sum())
    out.loc[fut, "lon"] = rng.uniform(30, 40, n)
    out.loc[fut, "lat"] = rng.uniform(50, 60, n)
    out.loc[fut, "speed"] = rng.integers(0, 300, n).astype(float)
    out.loc[fut, "valid"] = rng.random(n) > 0.5
    return out


def _same(a: dict, b: dict) -> bool:
    return all((np.isnan(a[k]) and np.isnan(b[k])) or a[k] == b[k] for k in FEATURES)


def test_returns_all_features_in_order(synth_plan, synth_track):
    f = point_features(synth_plan, synth_track, START + timedelta(minutes=2), 1006, 30.0)

    assert list(f) == FEATURES
    assert f["cur_dev"] == 30.0
    assert f["lead"] == pytest.approx(4.0)
    assert f["n_between"] == 4


def test_no_identifiers_or_fact_in_interface():
    params = list(inspect.signature(point_features).parameters)

    assert params == ["plan_tr", "track", "t", "visit_id", "cur_dev_s"]
    assert not any(k in FEATURES for k in ("tr_id", "visit_id", "unit_id", "target_stop_id"))
    assert not any("fact" in k for k in FEATURES)


def test_future_track_rows_do_not_change_features_synthetic(synth_plan, synth_track):
    t = START + timedelta(minutes=3, seconds=5)

    a = point_features(synth_plan, synth_track, t, 1008, 30.0)
    b = point_features(synth_plan, _corrupt_future(synth_track, t), t, 1008, 30.0)

    assert _same(a, b)
    assert a["gps_dev"] == pytest.approx(30.0, abs=12)


def test_none_cur_dev_and_empty_track(synth_plan, synth_track):
    f = point_features(synth_plan, synth_track.iloc[:0], START, 1005, None)

    assert np.isnan(f["cur_dev"]) and np.isnan(f["spd5"]) and np.isnan(f["gps_dev"])


def test_unknown_visit_raises(synth_plan, synth_track):
    with pytest.raises(KeyError):
        point_features(synth_plan, synth_track, START, 999, 0.0)


def test_future_rows_do_not_change_features_real_and_fast():
    plan = split_by_tr(load_plan(require_raw("test", "schedule.csv")))
    tracks = split_tracks(load_traffic(require_raw("test", "traffic.csv")))
    labels = pd.read_csv(require_raw("labels", "labels_test.csv")).sample(40, random_state=1)
    elapsed = 0.0
    for r in labels.itertuples():
        t = pd.Timestamp(r.T).to_pydatetime()
        tr = tracks[r.tr_id]
        start = time.perf_counter()
        a = point_features(plan[r.tr_id], tr, t, r.target_stop_id, r.cur_dev_s)
        elapsed += time.perf_counter() - start
        b = point_features(plan[r.tr_id], _corrupt_future(tr, t), t, r.target_stop_id, r.cur_dev_s)
        assert _same(a, b)
    assert elapsed / len(labels) < 0.005
