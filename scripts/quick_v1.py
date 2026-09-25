"""Быстрая модель v1 (LightGBM) для первого сабмита.

Временный скрипт: он закрепляет score на лидерборде в первый вечер, пока
основной пайплайн (``transit_core.features`` + CatBoost) в работе.

Правило честности соблюдается так:

* расписание читается только плановыми колонками — ``time_fact_begin``
  в признаки не попадает никогда;
* телеметрия берётся с ``event_time <= T``;
* для validate используется ``validate/schedule_plan.csv``, а не test-расписание.

Модель учит остаток ``target_delay_s - cur_dev_s``; прогноз = ``cur_dev_s`` + остаток.

Запуск::

    uv run python -m scripts.quick_v1
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW = REPO_ROOT / "data" / "raw"
OUT = REPO_ROOT / "submissions"

PLAN_COLS = ["tt_action_item_id", "tr_id", "time_begin", "manual_fill", "geom"]
TRIP_GAP_MIN = 5.0
SYNTHETIC_UNIT_MIN = 9_000_000
SYNTHETIC_WEIGHT = 0.5
SEEDS = 5
PARAMS = {
    "objective": "l1",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 30,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "feature_fraction": 0.8,
    "verbose": -1,
}
NUM_ROUNDS = 400
# Грубая поправка на косинус широты Москвы для расстояний в градусах.
LON_SCALE = 0.56
METERS_PER_DEGREE = 111_000


def load_plan(path: Path) -> pd.DataFrame:
    """Читает расписание без факта и размечает рейсы по разрыву плана."""
    s = pd.read_csv(path, usecols=PLAN_COLS)
    s["tb"] = pd.to_datetime(s["time_begin"], format="mixed")
    xy = s["geom"].str.extract(r"POINT \(([\d.]+) ([\d.]+)\)").astype(float)
    s["slon"], s["slat"] = xy[0], xy[1]
    s = s.sort_values(["tr_id", "tb"])
    s["gap"] = s.groupby("tr_id")["tb"].diff().dt.total_seconds() / 60
    s["new_trip"] = (s["gap"].isna() | (s["gap"] >= TRIP_GAP_MIN)).astype(int)
    return s[["tt_action_item_id", "tr_id", "tb", "slon", "slat", "gap", "new_trip", "manual_fill"]]


def load_traffic(path: Path) -> pd.DataFrame:
    """Читает валидные точки телеметрии, отсортированные по ТС и времени."""
    cols = ["tr_id", "unit_id", "event_time", "location_valid", "lon", "lat", "speed"]
    t = pd.read_csv(path, usecols=cols, low_memory=False)
    t = t[t["location_valid"] == True].copy()  # noqa: E712 — в CSV строки 'True'/'False'
    t["et"] = pd.to_datetime(t["event_time"], format="mixed")
    return t.sort_values(["tr_id", "et"])[["tr_id", "unit_id", "et", "lon", "lat", "speed"]]


def _distance_m(lon1, lat1, lon2, lat2):
    return np.sqrt(((lon1 - lon2) * LON_SCALE) ** 2 + (lat1 - lat2) ** 2) * METERS_PER_DEGREE


def _point_features(row, plan: pd.DataFrame, track: pd.DataFrame | None) -> dict:
    """Признаки одной прогнозной точки строго по данным до момента T."""
    t_now, t_target = row.T, row.ttb
    target = plan[plan["tt_action_item_id"] == row.target_stop_id].iloc[0]
    between = plan[(plan["tb"] > t_now) & (plan["tb"] <= t_target)]
    f = {
        "lead": (t_target - t_now).total_seconds() / 60,
        "hour": t_now.hour + t_now.minute / 60,
        "tgt_manual": int(target["manual_fill"]),
        "tgt_newtrip": int(target["new_trip"]),
        "tgt_gap": target["gap"],
        "trip_break_between": int(between["new_trip"].max()) if len(between) else 0,
        "max_gap_between": between["gap"].max() if len(between) else 0.0,
        "n_between": len(between),
        "cur_dev": row.cur_dev_s,
    }
    history = track[track["et"] <= t_now] if track is not None else None
    if history is None or history.empty:
        return f
    last = history.iloc[-1]
    w5 = history[history["et"] > t_now - pd.Timedelta("5min")]
    w15 = history[history["et"] > t_now - pd.Timedelta("15min")]
    f.update(
        stale=(t_now - last["et"]).total_seconds(),
        spd_last=last["speed"],
        spd5=w5["speed"].mean(),
        spd15=w15["speed"].mean(),
        stop5=(w5["speed"] < 3).mean(),
        dist_tgt=_distance_m(target["slon"], target["slat"], last["lon"], last["lat"]),
    )
    near = plan[
        (plan["tb"] >= t_now - pd.Timedelta("40min"))
        & (plan["tb"] <= t_now + pd.Timedelta("40min"))
    ]
    if len(near):
        d = _distance_m(near["slon"].values, near["slat"].values, last["lon"], last["lat"])
        j = int(d.argmin())
        f["gps_dev"] = (t_now - near["tb"].iloc[j]).total_seconds()
        f["gps_dist"] = d[j]
    return f


def build_features(points: pd.DataFrame, plan: pd.DataFrame, traffic: pd.DataFrame) -> pd.DataFrame:
    """Строит таблицу признаков для прогнозных точек (as-of T)."""
    p = points.copy()
    p["T"] = pd.to_datetime(p["T"])
    p["ttb"] = pd.to_datetime(p["target_time_begin"])
    plans = dict(tuple(plan.groupby("tr_id")))
    tracks = dict(tuple(traffic.groupby("tr_id")))
    rows = [_point_features(r, plans[r.tr_id], tracks.get(r.tr_id)) for r in p.itertuples()]
    return pd.DataFrame(rows, index=points.index)


def fit_predict(x_train, y_train, w_train, x_pred) -> np.ndarray:
    """Усреднение LightGBM по нескольким seed, чтобы снизить дисперсию."""
    preds = []
    for seed in range(SEEDS):
        booster = lgb.train(
            {**PARAMS, "seed": seed},
            lgb.Dataset(x_train, y_train, weight=w_train),
            num_boost_round=NUM_ROUNDS,
        )
        preds.append(booster.predict(x_pred))
    return np.mean(preds, axis=0)


def _weights(labels: pd.DataFrame, traffic: pd.DataFrame) -> np.ndarray:
    synthetic = set(traffic.loc[traffic["unit_id"] >= SYNTHETIC_UNIT_MIN, "tr_id"])
    return np.where(labels["tr_id"].isin(synthetic), SYNTHETIC_WEIGHT, 1.0)


def write_submission(points: pd.DataFrame, prediction: np.ndarray, path: Path) -> None:
    """Пишет сабмит в формате платформы: ``sample_id;prediction``."""
    out = pd.DataFrame({"sample_id": points["sample_id"], "prediction": np.round(prediction, 2)})
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, sep=";", index=False)


def main() -> int:
    lb_train = pd.read_csv(RAW / "labels" / "labels_train.csv")
    lb_test = pd.read_csv(RAW / "labels" / "labels_test.csv")
    points = pd.read_csv(RAW / "validate" / "points.csv")
    tr_traffic = load_traffic(RAW / "train" / "traffic.csv")
    te_traffic = load_traffic(RAW / "test" / "traffic.csv")
    va_traffic = load_traffic(RAW / "validate" / "traffic.csv")

    x_train = build_features(lb_train, load_plan(RAW / "train" / "schedule.csv"), tr_traffic)
    x_test = build_features(lb_test, load_plan(RAW / "test" / "schedule.csv"), te_traffic)
    x_val = build_features(points, load_plan(RAW / "validate" / "schedule_plan.csv"), va_traffic)
    cols = list(x_train.columns)
    x_test, x_val = x_test.reindex(columns=cols), x_val.reindex(columns=cols)

    # Оценка: учимся на train, проверяем на test (реальные ТС).
    y_train = (lb_train["target_delay_s"] - lb_train["cur_dev_s"]).to_numpy()
    w_train = _weights(lb_train, tr_traffic)
    pred_test = lb_test["cur_dev_s"] + fit_predict(x_train, y_train, w_train, x_test)
    y_test = lb_test["target_delay_s"]
    mae_base = float((y_test - lb_test["cur_dev_s"]).abs().mean())
    mae_model = float((y_test - pred_test).abs().mean())
    gain = 1 - mae_model / mae_base
    print(f"test: baseline MAE {mae_base:.2f} -> v1 MAE {mae_model:.2f} ({gain:.1%})")

    # Финал: train + test вместе, прогноз на validate.
    x_all = pd.concat([x_train, x_test], ignore_index=True)
    y_all = np.concatenate([y_train, (y_test - lb_test["cur_dev_s"]).to_numpy()])
    w_all = np.concatenate([w_train, np.ones(len(lb_test))])
    pred_val = points["cur_dev_s"].to_numpy() + fit_predict(x_all, y_all, w_all, x_val)

    write_submission(points, points["cur_dev_s"].to_numpy(), OUT / "sub_v0_baseline.csv")
    write_submission(points, pred_val, OUT / "sub_v1_lgbm.csv")
    report = {
        "model": "v1_lgbm",
        "features": cols,
        "test_mae_baseline": round(mae_base, 2),
        "test_mae_model": round(mae_model, 2),
        "test_improvement": round(gain, 4),
        "synthetic_weight": SYNTHETIC_WEIGHT,
        "seeds": SEEDS,
        "rounds": NUM_ROUNDS,
    }
    (OUT / "sub_v1_lgbm.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(f"validate: {len(points)} прогнозов, среднее {pred_val.mean():.1f} c")
    return 0


if __name__ == "__main__":
    sys.exit(main())
