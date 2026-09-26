"""Оценка стоп-детектора и онлайн-``cur_dev`` на train/test.

Единственное место (кроме :mod:`transit_core.labels`), где читается ``time_fact_begin`` —
только чтобы измерить ошибку виртуального факта. В признаки факт не идёт.

Запуск::

    uv run python -m scripts.eval_detector
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from transit_core.plan import load_plan, split_by_tr
from transit_core.stops_detector import detect_arrivals, online_cur_dev
from transit_core.track import load_traffic, split_tracks, synthetic_tr_ids

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "models" / "detector_metrics.json"


def _fact_delay(split: str) -> pd.Series:
    """Факт − план по визитам (только для оценки детектора)."""
    s = pd.read_csv(RAW / split / "schedule.csv",
                    usecols=["tt_action_item_id", "time_begin", "time_fact_begin"])
    d = pd.to_datetime(s["time_fact_begin"]) - pd.to_datetime(s["time_begin"])
    return pd.Series(d.dt.total_seconds().to_numpy(), index=s["tt_action_item_id"])


def _err_stats(err: pd.Series) -> dict:
    a = err.abs()
    return {"n": int(len(a)), "mae": round(float(a.mean()), 2),
            "median_ae": round(float(a.median()), 2), "p90_ae": round(float(a.quantile(0.9)), 2),
            "bias_median": round(float(err.median()), 2)}


def eval_detector(split: str) -> dict:
    """MAE виртуального факта против ``time_fact_begin`` по реальным ТС сплита."""
    plan = load_plan(RAW / split / "schedule.csv")
    traffic = load_traffic(RAW / split / "traffic.csv")
    plans, tracks = split_by_tr(plan), split_tracks(traffic)
    real = set(tracks) - synthetic_tr_ids(traffic)
    fact = _fact_delay(split)
    rows = []
    for tr_id in sorted(real & set(plans)):
        arr = detect_arrivals(plans[tr_id], tracks[tr_id], plans[tr_id]["tb"].max())
        rows.append(arr.merge(plans[tr_id][["visit_id", "manual_fill", "new_trip"]]))
    r = pd.concat(rows, ignore_index=True)
    r["err"] = r["delay_s"] - r["visit_id"].map(fact)
    n_plan = int(plan["tr_id"].isin(real).sum())
    auto = r[~r["manual_fill"]]
    return {"coverage": round(len(r) / n_plan, 4), "all": _err_stats(r["err"]),
            "manual_fill_false": _err_stats(auto["err"]),
            "manual_fill_false_mid_trip": _err_stats(auto.loc[auto["new_trip"] == 0, "err"])}


def eval_online_cur_dev(split: str, labels: str) -> dict:
    """MAE(online_cur_dev, cur_dev_s) на размеченных точках сплита."""
    plans = split_by_tr(load_plan(RAW / split / "schedule.csv"))
    tracks = split_tracks(load_traffic(RAW / split / "traffic.csv"))
    lb = pd.read_csv(RAW / "labels" / labels)
    est = [online_cur_dev(plans[r.tr_id], tracks[r.tr_id], pd.Timestamp(r.T).to_pydatetime())
           for r in lb.itertuples()]
    est = pd.Series([np.nan if e is None else e for e in est], index=lb.index)
    ok = est.notna()
    return {"coverage": round(float(ok.mean()), 4),
            "mae_vs_cur_dev_s": round(float((est[ok] - lb.loc[ok, "cur_dev_s"]).abs().mean()), 2),
            "mae_vs_target": round(float((est[ok] - lb.loc[ok, "target_delay_s"]).abs().mean()), 2),
            "mae_cur_dev_s_vs_target": round(float(
                (lb["cur_dev_s"] - lb["target_delay_s"]).abs().mean()), 2)}


def main() -> int:
    report = {"detector": {s: eval_detector(s) for s in ("train", "test")},
              "online_cur_dev": {"test": eval_online_cur_dev("test", "labels_test.csv"),
                                 "train": eval_online_cur_dev("train", "labels_train.csv")}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
