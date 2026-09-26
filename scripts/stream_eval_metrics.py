"""Метрики потокового прогона против реальной разметки ``labels_test``.

``validate/traffic.csv`` побайтно равен ``test/traffic.csv``, а ``labels_test`` — разметка
тех же ТС того же дня. Поэтому прогноз, который backend выдал на потоке в сим-минуту ``T``
для остановки ``target_stop_id``, можно сверить с фактической задержкой. Факт
(``target_delay_s``, ``time_fact_begin``) используется только здесь, для оценки.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from services.ml.app.dataset import build_split, with_cur_dev
from services.ml.app.inference import load_registry, predict, to_matrix
from transit_core.features import FEATURES
from transit_core.labels import LATE_S, load_schedule_with_fact
from transit_core.plan import load_plan

LEAD_LO, LEAD_HI = 10.0, 15.0
PARITY_TOL_S = 1.0
FEATURE_TOL = 1e-6
MISS_NO_VEHICLE = "нет ТС в state (нет телеметрии)"
MISS_WARMUP = "прогрев ТС (< 15 сим-мин истории)"
MISS_NO_VISITS = "у ТС нет визитов в окне"
MISS_NOT_IN_WINDOW = "цель не среди визитов окна"


def load_labels(data_dir: Path, start: datetime, end: datetime, tr_ids=None) -> pd.DataFrame:
    """Точки ``labels_test`` с ``T`` в окне прогона (и у выбранных ТС)."""
    lab = pd.read_csv(data_dir / "labels" / "labels_test.csv",
                      parse_dates=["T", "target_time_begin"])  # fmt: skip
    keep = (lab["T"] >= start) & (lab["T"] <= end)
    if tr_ids is not None:
        keep &= lab["tr_id"].isin(tr_ids)
    return lab[keep].reset_index(drop=True)


def label_keys(labels: pd.DataFrame) -> set[tuple[int, datetime]]:
    """Ключи ``(tr_id, T)`` для записи признаков на потоке."""
    return {(int(r.tr_id), r.T.to_pydatetime()) for r in labels.itertuples()}


def _miss_reason(minutes: pd.DataFrame, tr_id: int, t: pd.Timestamp) -> str:
    row = minutes[(minutes["tr_id"] == tr_id) & (minutes["T"] == t)]
    if row.empty:
        return MISS_NO_VEHICLE
    r = row.iloc[0]
    if not r["ready"]:
        return MISS_WARMUP
    return MISS_NO_VISITS if r["n_visits"] == 0 else MISS_NOT_IN_WINDOW


def match(labels: pd.DataFrame, preds: pd.DataFrame, minutes: pd.DataFrame) -> pd.DataFrame:
    """К каждой точке разметки — потоковый прогноз цели, сгенерированный ровно в ``T``."""
    cols = ["tr_id", "T", "visit_id", "delay_s", "p_late", "risk", "mode", "lead_min",
            "horizon_ok", "cur_dev", "stale", "first", "generated_at", "planned_at"]  # fmt: skip
    p = preds[cols].rename(columns={"visit_id": "target_stop_id", "delay_s": "stream_pred",
                                    "cur_dev": "stream_cur_dev"})  # fmt: skip
    m = labels.merge(p, on=["tr_id", "T", "target_stop_id"], how="left")
    m["found"] = m["stream_pred"].notna()
    m["miss_reason"] = [
        None if f else _miss_reason(minutes, int(tr), t)
        for f, tr, t in zip(m["found"], m["tr_id"], m["T"], strict=True)
    ]
    return m


def coverage(m: pd.DataFrame) -> dict:
    """Доля точек разметки, для цели которых на потоке был прогноз в момент ``T``."""
    found = m[m["found"]]
    return {
        "n_labels": len(m),
        "n_found": int(m["found"].sum()),
        "share": _r(m["found"].mean(), 4),
        "n_ml": int((found["mode"] == "ml").sum()),
        "n_fallback": int((found["mode"] != "ml").sum()),
        "target_is_first_in_window": _r(found["first"].astype(bool).mean(), 4),
        "miss_reasons": m["miss_reason"].dropna().value_counts().to_dict(),
    }


def horizon(preds: pd.DataFrame, m: pd.DataFrame) -> dict:
    """Инвариант горизонта по ВСЕМ потоковым прогнозам и упреждение до факта на метках."""
    gen = pd.to_datetime(preds["generated_at"])
    planned = pd.to_datetime(preds["planned_at"])
    ok = preds[preds["horizon_ok"]]
    lead = ok["lead_min"]
    in_window = (lead > LEAD_LO) & (lead <= LEAD_HI)
    hist = lead.apply(np.ceil).clip(LEAD_LO + 1, LEAD_HI).astype(int).value_counts().sort_index()
    found = m[m["found"]]
    fact_at = found["target_time_begin"] + pd.to_timedelta(found["target_delay_s"], unit="s")
    to_fact = (fact_at - pd.to_datetime(found["generated_at"])).dt.total_seconds() / 60
    return {
        "n_predictions": len(preds),
        "n_horizon_ok": len(ok),
        "horizon_ok_lead_in_window_share": _r(in_window.mean(), 4),
        "n_outside_window_flagged": int((~preds["horizon_ok"]).sum()),
        "outside_window_all_flagged_lead_gt_15": bool(
            (preds.loc[~preds["horizon_ok"], "lead_min"] > LEAD_HI).all()
        ),
        "generated_before_planned_share": _r((gen < planned).mean(), 4),
        "generated_at_equals_pass_minute_share": _r((gen == preds["T"]).mean(), 4),
        "lead_min": _describe(lead),
        "lead_hist_by_minute": {f"({k - 1},{k}]": int(v) for k, v in hist.items()},
        "labels_minutes_from_forecast_to_actual_arrival": _describe(to_fact),
        "labels_forecast_before_actual_share": _r((to_fact > 0).mean(), 4),
    }


def accuracy(m: pd.DataFrame) -> dict:
    """MAE потоковой модели против baseline'ов на найденных точках."""
    f = m[m["found"]]
    y = f["target_delay_s"].to_numpy(float)
    online = np.nan_to_num(f["stream_cur_dev"].to_numpy(float), nan=0.0)
    return {
        "n": len(f),
        "mae_stream_model": _mae(f["stream_pred"], y),
        "mae_baseline_cur_dev_s": _mae(f["cur_dev_s"].fillna(0.0), y),
        "mae_baseline_online_cur_dev": _mae(online, y),
        "mae_zero": _mae(np.zeros(len(y)), y),
        "online_cur_dev_available_share": _r(f["stream_cur_dev"].notna().mean(), 4),
    }


def offline_stream(m: pd.DataFrame, models_dir: Path) -> pd.DataFrame:
    """Stream-модель офлайн: признаки из CSV тем же кодом, что при обучении."""
    table = build_split("test")
    x, _ = with_cur_dev(table.x, table.meta, "stream")
    reg = load_registry(models_dir)
    preds = predict(reg.models["stream"], x[reg.features].to_numpy(float), reg.features, False)
    off = table.meta[["sample_id"]].assign(offline_pred=[p["delay_s"] for p in preds])
    off = pd.concat([off, x[FEATURES].add_prefix("off_")], axis=1)
    return m.merge(off, on="sample_id", how="left")


def parity(m: pd.DataFrame, stream_features: dict, models_dir: Path) -> dict:
    """Офлайн против потока: прогнозы и каждый признак на одних и тех же точках."""
    f = m[m["found"] & (m["mode"] == "ml")].copy()
    if f.empty:
        return {"n": 0}
    diff = (f["stream_pred"] - f["offline_pred"]).abs()
    rows = [stream_features.get((int(r.tr_id), r.T.to_pydatetime(), int(r.target_stop_id)), {})
            for r in f.itertuples()]  # fmt: skip
    reg = load_registry(models_dir)
    served = predict(reg.models["stream"], to_matrix(rows, reg.features), reg.features, False)
    serve_diff = f["stream_pred"].to_numpy() - np.array([p["delay_s"] for p in served])
    per_feature = {}
    for name in FEATURES:
        s = np.array([r.get(name, np.nan) for r in rows], dtype=float)
        o = f[f"off_{name}"].to_numpy(float)
        bad = ~((np.isnan(s) & np.isnan(o)) | (np.abs(s - o) <= FEATURE_TOL))
        if bad.any():
            per_feature[name] = {
                "n_mismatch": int(bad.sum()),
                "mean_abs_diff": _r(np.nanmean(np.abs(s - o)[bad]), 4),
            }
    worst = f.assign(diff=diff).nlargest(5, "diff")
    return {
        "n": len(f),
        "mean_abs_diff_s": _r(diff.mean(), 3),
        "max_abs_diff_s": _r(diff.max(), 3),
        "share_within_1s": _r((diff <= PARITY_TOL_S).mean(), 4),
        "mae_offline_stream_model": _mae(f["offline_pred"], f["target_delay_s"]),
        "mae_stream_same_points": _mae(f["stream_pred"], f["target_delay_s"]),
        "serving_mean_abs_diff_s": _r(np.abs(serve_diff).mean(), 3),
        "features_mismatch": per_feature,
        "worst": [
            {"sample_id": r.sample_id, "stream": r.stream_pred, "offline": r.offline_pred}
            for r in worst.itertuples()
        ],
    }


def alerts(incidents: list[dict], m: pd.DataFrame, data_dir: Path) -> dict:
    """Инциденты: упреждение, точность по факту расписания, precision/recall на метках."""
    fact = load_schedule_with_fact(data_dir / "test" / "schedule.csv").set_index("visit_id")
    rows = []
    for inc in incidents:
        vid = int(inc["target_stop"]["visit_id"])
        actual = fact["delay_s"].get(vid, np.nan)
        rows.append({"visit_id": vid, "tr_id": int(inc["tr_id"]), "lead_min": inc["lead_min"],
                     "created_at": inc["created_at"], "actual": actual,
                     "outcome_online": inc["outcome"]})  # fmt: skip
    inc = pd.DataFrame(rows, columns=["visit_id", "tr_id", "lead_min", "created_at", "actual",
                                      "outcome_online"])  # fmt: skip
    known, lead = inc[inc["actual"].notna()], inc["lead_min"]
    hits = known["actual"] > LATE_S
    f = m[m["found"]]
    red, late = f["risk"] == "red", f["target_class"] == "late"
    trip = load_plan(data_dir / "validate" / "schedule_plan.csv").set_index("visit_id")["trip"]
    late_pts = m[m["target_class"] == "late"]
    late_keys = set(zip(late_pts["tr_id"], late_pts["target_stop_id"], strict=True))
    late_trips = {(tr, trip[v]) for tr, v in late_keys}
    alerted = set(zip(inc["tr_id"], inc["visit_id"], strict=True))
    alerted_trips = {(tr, trip[v]) for tr, v in alerted}
    return {
        "incidents_opened": len(inc),
        "lead_ge_10_share": _r((lead >= LEAD_LO).mean(), 4) if len(inc) else None,
        "lead_in_window_share": _r(((lead > LEAD_LO) & (lead <= LEAD_HI)).mean(), 4)
        if len(inc)
        else None,
        "lead_min": _describe(inc["lead_min"]),
        "incident_precision_vs_schedule_fact": _r(hits.mean(), 4) if len(known) else None,
        "incidents_with_fact": len(known),
        "online_outcomes": inc["outcome_online"].value_counts().to_dict(),
        "points_red_vs_late": prf(red, late),
        "late_points_with_incident_on_target": _r(np.mean([k in alerted for k in late_keys]), 4)
        if late_keys
        else None,
        "late_trips_with_incident": _r(np.mean([k in alerted_trips for k in late_trips]), 4)
        if late_trips
        else None,
        "n_late_targets": len(late_keys),
        "n_late_trips": len(late_trips),
    }


def latency(passes: pd.DataFrame, wall_s: float) -> dict:
    """Время прохода прогнозов на сим-минуту (подготовка + ML + применение)."""
    busy = passes[passes["n_preds"] > 0]
    sim_min = len(passes)
    return {
        "passes": sim_min,
        "passes_with_predictions": len(busy),
        "pass_ms": _describe(busy["wall_ms"]),
        "ml_ms": _describe(busy["ml_ms"].dropna()),
        "preds_per_pass": _describe(busy["n_preds"]),
        "run_wall_s": _r(wall_s, 1),
        "sim_minutes_per_wall_s": _r(sim_min / wall_s, 2) if wall_s else None,
        "max_speed_without_skips": _r(60_000 / busy["wall_ms"].quantile(0.95), 0)
        if len(busy)
        else None,
        "modes": passes["mode"].value_counts().to_dict(),
    }


def prf(pred: pd.Series, true: pd.Series) -> dict:
    tp = int((pred & true).sum())
    fp, fn = int((pred & ~true).sum()), int((~pred & true).sum())
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": int((~pred & ~true).sum()),
            "precision": _r(p, 4), "recall": _r(r, 4)}  # fmt: skip


def _mae(pred, y) -> float | None:
    pred, y = np.asarray(pred, float), np.asarray(y, float)
    return _r(np.abs(pred - y).mean(), 2) if len(y) else None


def _describe(s: pd.Series) -> dict:
    s = pd.Series(s, dtype=float).dropna()
    if s.empty:
        return {"n": 0}
    q = s.quantile([0.05, 0.5, 0.95])
    return {"n": len(s), "min": _r(s.min(), 2), "p05": _r(q[0.05], 2), "p50": _r(q[0.5], 2),
            "mean": _r(s.mean(), 2), "p95": _r(q[0.95], 2), "max": _r(s.max(), 2)}  # fmt: skip


def _r(v, nd: int):
    return None if v is None or pd.isna(v) else round(float(v), nd)
