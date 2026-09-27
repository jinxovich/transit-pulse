"""Эксперимент: признак ``sched_dev_gps`` для stream-модели (честное CV).

``sched_dev_gps`` — GPS-отклонение от нитки графика на момент ``T`` по правилам онлайна
(:func:`services.backend.app.state.matched.matched_now`): последняя привязка матчера с
``t_s <= T``, ``on_route``, ``confidence >= 0.5``, не старше 120 с; значение —
:func:`transit_core.matching.schedule_deviation_s` на момент привязки (с защитой отстоя).
Дополнительно ``sched_dev_age_s`` (возраст привязки) и ``progress_to_target_m`` (путь по
нитке до целевой остановки, если она на текущем рейсе).

Трек каждого ТС один раз прогоняется через :func:`transit_core.matching.match_track`
(каждая строка зависит только от точек до неё), признак — as-of поиск по ``T``.

Варианты CV stream-модели (схема ``block`` + clone guard, веса синтетики 0 и 0.5, те же
гиперпараметры, что в ``train.py``):

* ``base`` — как сейчас (``FEATURES``, база = ``cur_dev_online``);
* ``feat`` — плюс три новых признака;
* ``feat_fill`` — плюс признаки, а база (``cur_dev``) при пустом ``cur_dev_online``
  берётся из ``sched_dev_gps``.

Результат → ``models/sched_dev_experiment.json``.

Запуск::

    uv run python -m scripts.exp_sched_dev_stream [base|feat|feat_fill ...]
"""

from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from services.ml.app import cv
from services.ml.app import models as M
from services.ml.app.dataset import CACHE, RAW, SPLITS, clone_sources, labeled, with_cur_dev
from services.ml.app.train import catboost_cv
from transit_core.features import FEATURES
from transit_core.matching import MatchResult, RouteMatcher, match_track, schedule_deviation_s
from transit_core.plan import load_plan, split_by_tr
from transit_core.track import load_traffic, split_tracks

ROOT = CACHE.parents[1]
OUT = ROOT / "models" / "sched_dev_experiment.json"
MATCH_FRESH_S = 120.0
MIN_CONFIDENCE = 0.5
NEW_FEATURES = ["sched_dev_gps", "sched_dev_age_s", "progress_to_target_m"]
WEIGHTS = (0.0, 0.5)
CB_THREADS = 4
GAIN_THRESHOLD_S = 2.0
"""Отсечка: признак оформляется, если MAE stream-модели (w=0.5) лучше хотя бы на столько."""
NS = 1_000_000_000
_NAN = float("nan")
log = logging.getLogger("exp_sched_dev")


def run_vehicle(plan_tr: pd.DataFrame, track: pd.DataFrame) -> tuple[RouteMatcher, pd.DataFrame]:
    """Один проход матчера по треку ТС; к привязкам добавляется ``dev_s`` на их момент."""
    matcher = RouteMatcher(plan_tr)
    m = match_track(matcher, track)
    dev = []
    for r in m.itertuples(index=False):
        line = matcher.line_of(int(r.trip))
        mr = MatchResult(r.t_s, int(r.trip), r.progress_m, int(r.segment_idx), r.offset_m,
                         0, None, None, bool(r.on_route), r.confidence)  # fmt: skip
        dev.append(schedule_deviation_s(mr, line, r.t_s) if line is not None else _NAN)
    return matcher, m.assign(dev_s=np.asarray(dev, dtype=float))


def sched_dev_asof(
    matcher: RouteMatcher, m: pd.DataFrame, t_s: float, target_row: int | None
) -> tuple[float, float, float]:
    """``(sched_dev_gps, sched_dev_age_s, progress_to_target_m)`` на момент ``t_s``.

    Как ``matched_now``: берётся ровно последняя привязка до ``t_s``; если она не на
    маршруте, неуверенная или несвежая — NaN (более ранние привязки не ищутся).
    """
    j = int(np.searchsorted(m["t_s"].to_numpy(), t_s, side="right")) - 1
    if j < 0:
        return _NAN, _NAN, _NAN
    age = t_s - float(m["t_s"].iat[j])
    if not m["on_route"].iat[j] or m["confidence"].iat[j] < MIN_CONFIDENCE or age > MATCH_FRESH_S:
        return _NAN, _NAN, _NAN
    prog = _NAN
    line = matcher.line_of(int(m["trip"].iat[j]))
    if line is not None and target_row is not None:
        k = np.flatnonzero(line.rows == target_row)
        if len(k):
            prog = float(line.cum[k[0]] - m["progress_m"].iat[j])
    return float(m["dev_s"].iat[j]), age, prog


def build_split(name: str) -> pd.DataFrame:
    """Новые признаки для всех точек сплита (порядок строк — как в ``points``/``labels``)."""
    plan_f, traffic_f, points_f = SPLITS[name]
    plans = split_by_tr(load_plan(RAW / plan_f))
    tracks = split_tracks(load_traffic(RAW / traffic_f))
    pts = pd.read_csv(RAW / points_f)
    pts["T"] = pd.to_datetime(pts["T"], format="ISO8601")
    runs = {tr: run_vehicle(plans[tr], tracks[tr]) for tr in pts["tr_id"].unique()
            if tr in tracks}  # fmt: skip
    rows = []
    for r in pts.itertuples():
        t_s = float(np.datetime64(r.T.to_pydatetime(), "ns").astype(np.int64)) / NS
        if r.tr_id not in runs:
            rows.append((_NAN, _NAN, _NAN))
            continue
        hits = np.flatnonzero(plans[r.tr_id]["visit_id"].to_numpy() == r.target_stop_id)
        rows.append(sched_dev_asof(*runs[r.tr_id], t_s, int(hits[0]) if len(hits) else None))
    return pd.DataFrame(rows, columns=NEW_FEATURES)


def load_feature(name: str, refresh: bool = False) -> pd.DataFrame:
    """Признаки сплита с кешем ``data/cache/sched_dev_<split>.pkl``."""
    path = CACHE / f"sched_dev_{name}.pkl"
    if path.exists() and not refresh:
        return pd.read_pickle(path)
    df = build_split(name)
    pd.to_pickle(df, path)
    return df


def leak_check(name: str = "test", n_points: int = 40, seed: int = 0) -> dict:
    """Порча трека после ``T`` (сдвиг координат, мусорные точки) не меняет признак."""
    plan_f, traffic_f, points_f = SPLITS[name]
    plans = split_by_tr(load_plan(RAW / plan_f))
    tracks = split_tracks(load_traffic(RAW / traffic_f))
    pts = pd.read_csv(RAW / points_f)
    pts["T"] = pd.to_datetime(pts["T"], format="ISO8601")
    sample = pts.sample(n_points, random_state=seed)
    rng = np.random.default_rng(seed)
    n_diff = n_valid = 0
    for r in sample.itertuples():
        t64 = np.datetime64(r.T.to_pydatetime(), "ns")
        t_s = float(t64.astype(np.int64)) / NS
        track = tracks[r.tr_id]
        after = track["et"].to_numpy(dtype="datetime64[ns]") > t64
        bad = track.copy()
        bad.loc[after, "lon"] = bad.loc[after, "lon"] + rng.normal(0, 0.01, after.sum())
        bad.loc[after, "lat"] = bad.loc[after, "lat"] + rng.normal(0, 0.01, after.sum())
        bad.loc[after, "speed"] = rng.uniform(0, 90, after.sum())
        ti = int(np.flatnonzero(plans[r.tr_id]["visit_id"].to_numpy() == r.target_stop_id)[0])
        a = sched_dev_asof(*run_vehicle(plans[r.tr_id], track), t_s, ti)
        b = sched_dev_asof(*run_vehicle(plans[r.tr_id], bad), t_s, ti)
        n_valid += int(np.isfinite(a[0]))
        n_diff += int(not np.allclose(a, b, equal_nan=True))
    return {"split": name, "n_points": n_points, "n_with_value": n_valid, "n_changed": n_diff}


def _table_with_features():
    table = labeled()
    feats = pd.concat([load_feature(s) for s in ("train", "test")], ignore_index=True)
    assert len(feats) == len(table.meta)
    return table, feats


def variant_xy(name: str, table, feats) -> tuple[pd.DataFrame, np.ndarray]:
    """Матрица признаков и база stream-модели для варианта эксперимента."""
    x, base = with_cur_dev(table.x, table.meta, "stream")
    if name == "base":
        return x[FEATURES], base
    x = pd.concat([x[FEATURES], feats], axis=1)
    if name == "feat_fill":
        cur = x["cur_dev"].to_numpy(dtype=float)
        x["cur_dev"] = np.where(np.isnan(cur), x["sched_dev_gps"].to_numpy(), cur)
        base = np.nan_to_num(x["cur_dev"].to_numpy(dtype=float), nan=0.0)
    elif name != "feat":
        raise ValueError(name)
    return x, base


def run_variant(name: str) -> dict:
    """CV stream-модели для варианта: MAE при весах синтетики 0 и 0.5."""
    M.CB_PARAMS["thread_count"] = CB_THREADS
    table, feats = _table_with_features()
    sources = clone_sources()
    x, base = variant_xy(name, table, feats)
    y = table.meta["target_delay_s"].to_numpy(dtype=float)
    real = cv.real_positions(table.meta)
    out = {"baseline_mae": float(np.mean(np.abs(base[real] - y[real])))}
    for w in WEIGHTS:
        r = catboost_cv(x, base, y, table.meta, sources, w)
        out[f"w={w}"] = {k: v for k, v in r.items() if k != "oof"}
        log.info("%s w=%.1f MAE %.2f", name, w, r["mae"])
    return out


def coverage(table, feats) -> dict:
    """Доля точек с признаком: все / реальные / где ``cur_dev_online`` пуст."""
    real = ~table.meta["synthetic"].to_numpy()
    has = feats["sched_dev_gps"].notna().to_numpy()
    no_online = table.meta["cur_dev_online"].isna().to_numpy()
    y = table.meta["target_delay_s"].to_numpy(dtype=float)
    sd = feats["sched_dev_gps"].to_numpy()
    both = real & has & ~no_online
    online = table.meta["cur_dev_online"].to_numpy()
    return {
        "all": round(float(has.mean()), 4), "real": round(float(has[real].mean()), 4),
        "real_where_online_nan": round(float(has[real & no_online].mean()), 4),
        "n_real_online_nan": int((real & no_online).sum()),
        "real_mae_vs_target_where_both": {
            "n": int(both.sum()),
            "sched_dev_gps": round(float(np.abs(sd[both] - y[both]).mean()), 2),
            "cur_dev_online": round(float(np.abs(online[both] - y[both]).mean()), 2)},
        "progress_to_target_coverage_real": round(float(
            feats["progress_to_target_m"].notna().to_numpy()[real].mean()), 4),
    }  # fmt: skip


def decision(delta: dict) -> str:
    """Вердикт по отсечке :data:`GAIN_THRESHOLD_S` (по лучшему варианту при w=0.5)."""
    best = min(d["w=0.5"] for d in delta.values())
    if best <= -GAIN_THRESHOLD_S:
        return f"прирост {-best:.2f} с ≥ {GAIN_THRESHOLD_S} с — оформлять признак"
    return f"проверили — прироста нет: лучший {-best:.2f} с < {GAIN_THRESHOLD_S} с"


def _init_worker() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    variants = argv or ["base", "feat", "feat_fill"]
    table, feats = _table_with_features()
    val = load_feature("validate")
    res = {"coverage": coverage(table, feats),
           "coverage_validate": round(float(val["sched_dev_gps"].notna().mean()), 4),
           "leak_check": leak_check()}  # fmt: skip
    log.info("coverage %s leak %s", res["coverage"], res["leak_check"])
    with ProcessPoolExecutor(len(variants), initializer=_init_worker) as ex:
        res["cv"] = dict(zip(variants, ex.map(run_variant, variants), strict=True))
    b = res["cv"].get("base")
    if b:
        res["delta_vs_base"] = {
            v: {w: round(r[w]["mae"] - b[w]["mae"], 2) for w in (f"w={x}" for x in WEIGHTS)}
            for v, r in res["cv"].items()
        }
        res["decision"] = decision(res["delta_vs_base"])
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=float), "utf-8")
    log.info("%s", json.dumps(res.get("delta_vs_base"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
