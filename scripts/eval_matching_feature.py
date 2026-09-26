"""Сколько дал бы признак ``sched_dev_gps`` (отклонение от нитки графика по матчингу).

В модели признак не добавлен (они уже обучены) — здесь только оценка на реальных точках
labels (train + test): MAE как самостоятельного прогноза цели в сравнении с ``cur_dev_s``,
онлайн-детектором и существующим ``gps_dev`` (проекция без состояния), и выигрыш
смешивания ``oof + β·(sched_dev_gps − oof)`` с OOF-прогнозом CatBoost (β — по фолдам ТС).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from transit_core.features import point_features
from transit_core.matching import RouteMatcher
from transit_core.stops_detector import online_cur_dev

ROOT = Path(__file__).resolve().parent.parent
OOF = ROOT / "models" / "oof_catboost_honest.csv"
FRESH_S = 120.0
BETAS = np.round(np.arange(0.0, 0.51, 0.05), 2)
NS = 1_000_000_000


def sched_dev_at(matcher: RouteMatcher, m: pd.DataFrame, t_s: float) -> float:
    """As-of отклонение по последней привязке не старше :data:`FRESH_S` (иначе NaN)."""
    j = int(np.searchsorted(m["t_s"].to_numpy(), t_s, side="right")) - 1
    if j < 0 or t_s - m["t_s"].iat[j] > FRESH_S or not m["on_route"].iat[j]:
        return np.nan
    line = matcher.line_of(int(m["trip"].iat[j]))
    return t_s - line.planned_at(float(m["progress_m"].iat[j]), t_s) if line else np.nan


def _labels(raw: Path, plans: dict) -> pd.DataFrame:
    """Реальные точки labels_train и labels_test, для которых есть план ТС."""
    lb = pd.concat(
        [pd.read_csv(raw / "labels" / f) for f in ("labels_train.csv", "labels_test.csv")]
    )
    lb = lb[lb["tr_id"].isin(set(plans))].drop_duplicates("sample_id")
    lb["T"] = pd.to_datetime(lb["T"])
    return lb.reset_index(drop=True)


def _row(r, plans, tracks, matches) -> tuple[float, float, float]:
    """(sched_dev_gps, gps_dev, online_cur_dev) для точки labels."""
    matcher, m = matches[r.tr_id]
    t = r.T.to_pydatetime()
    t_s = np.datetime64(t, "ns").astype(np.int64) / NS
    sd = sched_dev_at(matcher, m, float(t_s))
    feats = point_features(plans[r.tr_id], tracks[r.tr_id], t, int(r.target_stop_id), None)
    det = online_cur_dev(plans[r.tr_id], tracks[r.tr_id], t)
    return sd, feats["gps_dev"], np.nan if det is None else det


def _blend_gain(df: pd.DataFrame) -> dict:
    """MAE OOF и OOF + β·(sched_dev_gps − OOF); β подбирается leave-one-ТС-out."""
    d = df.dropna(subset=["sched_dev_gps", "oof"])
    pred = np.empty(len(d))
    for tr in d["tr_id"].unique():
        fit, te = d["tr_id"] != tr, d["tr_id"] == tr
        z_fit = d.loc[fit, "sched_dev_gps"] - d.loc[fit, "oof"]
        maes = [(d.loc[fit, "oof"] + b * z_fit - d.loc[fit, "y"]).abs().mean() for b in BETAS]
        b = BETAS[int(np.argmin(maes))]
        pred[te.to_numpy()] = d.loc[te, "oof"] + b * (d.loc[te, "sched_dev_gps"] - d.loc[te, "oof"])
    resid = d["y"] - d["oof"]
    return {"n": len(d), "mae_oof": round(float((d["oof"] - d["y"]).abs().mean()), 2),
            "mae_blend_loto": round(float(np.abs(pred - d["y"]).mean()), 2),
            "corr_resid_vs_sched_minus_oof": round(float(np.corrcoef(
                resid, d["sched_dev_gps"] - d["oof"])[0, 1]), 3)}  # fmt: skip


def feature_report(raw: Path, plans: dict, tracks: dict, matches: dict) -> dict:
    """Отчёт по признаку ``sched_dev_gps`` на реальных точках labels."""
    lb = _labels(raw, {k: v for k, v in plans.items() if k in matches})
    vals = [_row(r, plans, tracks, matches) for r in lb.itertuples()]
    lb[["sched_dev_gps", "gps_dev", "online_cur_dev"]] = np.asarray(vals, dtype=float)
    if OOF.is_file():
        oof = pd.read_csv(OOF).groupby("sample_id")["oof_pred"].mean()
        lb["oof"] = lb["sample_id"].map(oof)
    y = lb["target_delay_s"]
    common = lb.dropna(subset=["sched_dev_gps", "gps_dev", "online_cur_dev"])
    yc = common["target_delay_s"]
    mae = {c: round(float((common[c] - yc).abs().mean()), 2)
           for c in ("cur_dev_s", "online_cur_dev", "gps_dev", "sched_dev_gps")}  # fmt: skip
    sd = lb["sched_dev_gps"]
    out = {"n_points": len(lb), "coverage": round(float(sd.notna().mean()), 4),
           "n_common": len(common), "mae_vs_target_common": mae,
           "mae_vs_cur_dev_s": round(float((sd - lb["cur_dev_s"]).abs().mean()), 2),
           "corr_with_target": round(float(sd.corr(y)), 3)}  # fmt: skip
    if "oof" in lb:
        out["blend_with_catboost_oof"] = _blend_gain(lb.rename(columns={"target_delay_s": "y"}))
    return out
