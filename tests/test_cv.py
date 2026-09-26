"""Схема CV: группы не рвутся, синтетика не попадает в валидацию, фильтр клонов работает."""

import numpy as np
import pandas as pd

from services.ml.app import cv


def _meta() -> pd.DataFrame:
    rows = []
    t0 = pd.Timestamp("2026-01-06 08:00")
    for tr in (1, 2, 3, 4):
        for b in range(5):
            for k in range(6):
                rows.append({"tr_id": tr, "T": t0 + pd.Timedelta(minutes=60 * b + 5 * k),
                             "block": f"r{tr}-{b}", "synthetic": False})
    for clone, _src in ((101, 1), (102, 2)):
        for k in range(24):
            rows.append({"tr_id": clone, "T": t0 + pd.Timedelta(minutes=15 * k),
                         "block": f"s{clone}", "synthetic": True})
    return pd.DataFrame(rows)


SOURCES = {101: 1, 102: 2}


def test_folds_keep_blocks_together_and_cover_real_points():
    meta = _meta()
    real = cv.real_positions(meta)

    folds = cv.make_folds(meta)

    assert len(folds) == cv.REPEATS
    for fold_ids in folds:
        assert len(fold_ids) == len(real)
        per_block = pd.Series(fold_ids).groupby(meta["block"].to_numpy()[real]).nunique()
        assert (per_block == 1).all()
        assert set(fold_ids) == set(range(cv.N_SPLITS))
    assert not np.array_equal(folds[0], folds[1])


def test_folds_are_reproducible():
    meta = _meta()

    assert all(np.array_equal(a, b) for a, b in zip(cv.make_folds(meta), cv.make_folds(meta),
                                                    strict=True))


def test_honest_filter_drops_clone_points_near_validation():
    meta = _meta()
    val = np.flatnonzero((meta["tr_id"] == 1) & (meta["block"] == "r1-0"))

    naive = cv.allowed_synthetic(meta, val, SOURCES, honest=False)
    honest = cv.allowed_synthetic(meta, val, SOURCES, honest=True)

    assert len(naive) == 48
    dropped = meta.iloc[np.setdiff1d(naive, honest)]
    assert set(dropped["tr_id"]) == {101}
    gap = dropped["T"].apply(lambda t: (meta.iloc[val]["T"] - t).abs().min())
    assert (gap <= cv.CLONE_GUARD).all()
    assert len(dropped) > 0


def test_run_cv_never_trains_on_validation_points():
    meta = _meta()
    x = pd.DataFrame({"i": np.arange(len(meta), dtype=float)})
    y = np.zeros(len(meta))
    seen = []

    def fit_predict(x_tr, y_tr, w_tr, x_val):
        assert not set(x_tr["i"]) & set(x_val["i"])
        assert np.all(w_tr[meta["synthetic"].to_numpy()[x_tr["i"].astype(int)]] == 0.5)
        seen.append(len(x_val))
        return np.zeros(len(x_val))

    oof = cv.run_cv(fit_predict, x, y, meta, 0.5, SOURCES)

    assert oof.shape == (cv.REPEATS, len(cv.real_positions(meta)))
    assert not np.isnan(oof).any()
    assert sum(seen) == cv.REPEATS * len(cv.real_positions(meta))
