"""Калибровка p_late и интервалов: метрики, корзины lead, conformal-масштабы, изотоника."""

import json

import numpy as np
import pytest

from services.ml.app import calibration as C
from services.ml.app.inference import Calibration, lead_bin_index, widen

LEADS = np.array([11.0, 12.0, 13.0, 14.0, 15.0])
LEAD_P = np.array([0.6, 0.25, 0.07, 0.04, 0.04])
N, R = 1500, 3


def _synthetic_oof(seed: int = 0):
    """OOF как у train: сырые квантили остатка узкие, разброс растёт с lead."""
    rng = np.random.default_rng(seed)
    lead = rng.choice(LEADS, size=N, p=LEAD_P)
    base = rng.normal(60, 80, size=N)
    sigma = 30 + 25 * (lead - 11)
    y = base + rng.normal(0, sigma)
    q = np.empty((R, N, 3))
    for r in range(R):
        med = rng.normal(0, 5, size=N)
        half = 20 + rng.uniform(0, 10, size=N)
        q[r] = np.stack([med - half, med, med + half], axis=1)
    folds = np.stack([rng.integers(0, 5, size=N) for _ in range(R)])
    return q, base, y, lead, folds


def test_brier_and_ece_known_values():
    p = np.array([0.0, 1.0, 0.5, 0.5])
    event = np.array([0, 1, 1, 0], dtype=bool)

    assert C.brier(p, event) == pytest.approx(0.125)
    assert C.ece(p, event) == pytest.approx(0.0)
    assert C.ece(np.full(4, 0.9), np.zeros(4, dtype=bool)) == pytest.approx(0.9)


def test_reliability_table_bins_cover_all_points():
    rng = np.random.default_rng(1)
    p = rng.uniform(size=2000)
    event = rng.uniform(size=2000) < p

    table = C.reliability(p, event)

    assert sum(row["n"] for row in table) == 2000
    assert all(0 <= row["p_mean"] <= 1 and 0 <= row["freq"] <= 1 for row in table)
    assert C.ece(p, event) < 0.05


def test_lead_bins_merge_small_neighbours():
    lead = np.repeat([11.0, 12.0, 13.0, 14.0, 15.0], [946, 402, 67, 38, 41])

    bins = C.lead_bins(lead, min_n=40)

    assert bins == [(10.0, 11.0), (11.0, 12.0), (12.0, 13.0), (13.0, 15.0)]


def test_lead_bins_merge_tail_into_previous():
    lead = np.repeat([11.0, 12.0, 15.0], [100, 100, 5])

    bins = C.lead_bins(lead, min_n=40)

    assert bins == [(10.0, 11.0), (11.0, 15.0)]


def test_lead_bin_index_right_inclusive_and_clipped():
    cuts = np.array([11.0, 12.0, 13.0])

    idx = lead_bin_index(np.array([9.0, 11.0, 11.5, 12.0, 15.0, 30.0, np.nan]), cuts)

    assert idx.tolist() == [0, 0, 1, 1, 3, 3, -1]


def test_isotonic_knots_monotone_and_bounded():
    rng = np.random.default_rng(2)
    p = rng.uniform(size=3000)
    event = rng.uniform(size=3000) < p**2

    x, y = C.fit_isotonic(p, event)

    assert np.all(np.diff(x) > 0)
    assert np.all(np.diff(y) >= 0)
    assert np.all((y >= 0) & (y <= 1))


def test_lead_scales_reach_target_coverage_per_bin():
    q, base, y, lead, folds = _synthetic_oof()

    art, report = C.evaluate(q, base, y, lead, folds, global_scale=2.0)
    cal = Calibration.from_dict(art)
    scale = cal.scale_for(np.tile(lead, R))
    q_abs = widen(q.reshape(-1, 3), scale) + np.tile(base, R)[:, None]
    ytile, idx = np.tile(y, R), lead_bin_index(np.tile(lead, R), cal.cuts)

    for b in range(len(cal.scales)):
        m = idx == b
        cov = np.mean((q_abs[m, 0] <= ytile[m]) & (ytile[m] <= q_abs[m, 2]))
        assert cov == pytest.approx(C.TARGET_COVERAGE, abs=0.03)
    assert np.all(np.diff(cal.scales) > 0)  # разброс растёт с lead → и масштаб
    for cov in report["coverage_by_lead"]["after"]:
        assert cov == pytest.approx(C.TARGET_COVERAGE, abs=0.05)


def test_evaluate_report_improves_probability_quality():
    q, base, y, lead, folds = _synthetic_oof()

    art, report = C.evaluate(q, base, y, lead, folds, global_scale=1.0)

    assert report["brier_after"] < report["brier_before"]
    assert report["ece_after"] < report["ece_before"]
    assert len(report["reliability_before"]) > 0 and len(report["reliability_after"]) > 0
    assert set(report["half_width_by_lead"]) == {"bins", "n", "before", "after"}
    assert art["err_k"] > 0 and art["version"] == C.VERSION
    json.dumps(art)
    json.dumps(report)


@pytest.mark.parametrize("mode", C.MODES)
def test_real_oof_coverage_per_lead_bin_and_monotone_isotonic(mode):
    npz, art_path = C.CACHE / f"oof_{mode}.npz", C.MODELS / f"calibration_{mode}.json"
    if not (npz.exists() and art_path.exists()):
        pytest.skip("нет кеша OOF или калибровки — запустите services.ml.app.train")
    d = np.load(npz)
    art = json.loads(art_path.read_text("utf-8"))
    cal = Calibration.from_dict(art)
    n_rep = d["q"].shape[0]
    qa = (d["q"] + d["base"][None, :, None]).reshape(-1, 3)
    lead, y = np.tile(d["lead"], n_rep), np.tile(d["y"], n_rep)
    q = widen(qa, cal.scale_for(lead))
    idx = lead_bin_index(lead, cal.cuts)

    for b in range(len(cal.scales)):
        m = idx == b
        cov = np.mean((q[m, 0] <= y[m]) & (y[m] <= q[m, 2]))
        assert cov == pytest.approx(C.TARGET_COVERAGE, abs=0.03), art["lead_bins"][b]
    assert np.all(np.diff(cal.iso_x) > 0) and np.all(np.diff(cal.iso_y) >= 0)
    assert art["quality"]["ece_after"] < art["quality"]["ece_before"]


def test_calibration_p_late_clipped_to_unit_interval():
    cal = Calibration.from_dict({
        "version": 1, "lead_bins": [{"lo": 10, "hi": 15, "scale": 2.0, "n": 100}],
        "global_scale": 2.0, "err_k": 1.0,
        "isotonic": {"x": [0.1, 0.5, 0.9], "y": [0.0, 0.3, 1.0]}})

    p = cal.p_late(np.array([-1.0, 0.0, 0.3, 0.95, 2.0]))

    assert np.all((p >= 0) & (p <= 1))
    assert np.all(np.diff(p) >= 0)
