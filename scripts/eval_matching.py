"""Офлайн-оценка map matching на реальных ТС train (``unit_id < 9e6``).

Метрики → ``models/matching_metrics.json``:

* **привязка:** доля валидных точек в окнах рейсов, получивших привязку ``on_route``;
  медиана/p90 поперечного отклонения; нарушения монотонности прогресса; латентность;
* **прибытия по пересечению прогресса** (``crossing``) против ``time_fact_begin`` — и
  стоп-детектор :mod:`transit_core.stops_detector` на тех же визитах;
* **онлайн-отклонение на момент прибытия** (``at_arrival``): что показал бы поток в момент
  фактического прибытия — :func:`transit_core.matching.schedule_deviation_s` по последней
  привязке против ``online_cur_dev`` детектора;
* **признак ``sched_dev_gps``** (не добавлен в модели): MAE против цели labels и
  выигрыш смешивания с OOF-прогнозом CatBoost.

``time_fact_begin`` читается только здесь, для оценки; в признаки не идёт.

Запуск::

    uv run python -m scripts.eval_matching
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.eval_detector import _err_stats, _fact_delay
from scripts.eval_matching_feature import feature_report, sched_dev_at
from transit_core.matching import RouteMatcher, match_track
from transit_core.plan import load_plan, split_by_tr
from transit_core.route_line import TripLine
from transit_core.stops_detector import detect_arrivals, online_cur_dev
from transit_core.track import load_traffic, split_tracks, synthetic_tr_ids

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "models" / "matching_metrics.json"
DEPART_M = 35.0
WINDOW_PAD_S = 5 * 60
MAX_CROSS_GAP_S = 120.0
NS = 1_000_000_000


def _crossings(line: TripLine, m: pd.DataFrame) -> list[tuple[int, float]]:
    """Время пересечения прогрессом дистанции каждой остановки рейса (интерполяция).

    Первая остановка рейса — отправление: пересечение :data:`DEPART_M` от её начала.
    Пересечения через разрыв данных длиннее :data:`MAX_CROSS_GAP_S` не считаются.
    """
    g = m[(m["trip"] == line.trip) & m["on_route"]]
    if len(g) < 2:
        return []
    p, t = g["progress_m"].to_numpy(), g["t_s"].to_numpy()
    out = []
    for k, row in enumerate(line.rows):
        target = DEPART_M if k == 0 else line.cum[k]
        j = int(np.searchsorted(p, target, side="left"))
        if j == 0 or j >= len(p) or t[j] - t[j - 1] > MAX_CROSS_GAP_S:
            continue
        frac = (target - p[j - 1]) / (p[j] - p[j - 1]) if p[j] > p[j - 1] else 1.0
        out.append((int(row), float(t[j - 1] + frac * (t[j] - t[j - 1]))))
    return out


def _run_vehicle(
    plan_tr: pd.DataFrame, track: pd.DataFrame
) -> tuple[RouteMatcher, pd.DataFrame, float]:
    """Прогон матчера по треку ТС: (матчер, привязки, секунд на точку)."""
    matcher = RouteMatcher(plan_tr)
    t0 = time.perf_counter()
    m = match_track(matcher, track)
    return matcher, m, (time.perf_counter() - t0) / max(len(track), 1)


def _in_trip_windows(matcher: RouteMatcher, t_s: np.ndarray) -> np.ndarray:
    """Маска моментов внутри плановых окон рейсов (± :data:`WINDOW_PAD_S`)."""
    mask = np.zeros(len(t_s), dtype=bool)
    for ln in matcher.lines:
        mask |= (t_s >= ln.t_start - WINDOW_PAD_S) & (t_s <= ln.t_end + WINDOW_PAD_S)
    return mask


def _quality(matcher: RouteMatcher, m: pd.DataFrame, track: pd.DataFrame) -> dict:
    """Счётчики привязки одного ТС."""
    v = track[track["valid"]]
    vt = v["et"].to_numpy(dtype="datetime64[ns]").astype(np.int64) / NS
    in_win = _in_trip_windows(matcher, vt)
    mt = m["t_s"].to_numpy()
    on_win = m["on_route"].to_numpy() & _in_trip_windows(matcher, mt)
    same = m["trip"].to_numpy()[1:] == m["trip"].to_numpy()[:-1]
    back = same & (np.diff(m["progress_m"].to_numpy()) < -1e-6)
    tracking = ~m["acquired"].to_numpy()[1:]
    return {
        "valid_in_windows": int(in_win.sum()),
        "on_route_in_windows": int(on_win.sum()),
        "matched": len(m),
        "back_steps_tracking": int((back & tracking).sum()),
        "reacquire_resets": int((back & ~tracking).sum()),
        "steps": int(same.sum()),
    }


def _arrivals(plan_tr, track, matcher, m, fact) -> pd.DataFrame:
    """Прибытия по пересечению и по детектору против факта (по визитам ТС)."""
    rows = [(r, t) for ln in matcher.lines for r, t in _crossings(ln, m)]
    cross = pd.DataFrame(rows, columns=["row", "t_cross"])
    tb = plan_tr["tb"].to_numpy(dtype="datetime64[ns]").astype(np.int64) / NS
    cross["visit_id"] = plan_tr["visit_id"].to_numpy()[cross["row"]]
    cross["dev_match"] = cross["t_cross"] - tb[cross["row"]]
    det = detect_arrivals(plan_tr, track, plan_tr["tb"].max())
    out = plan_tr[["visit_id", "manual_fill", "new_trip"]].copy()
    out["fact"] = out["visit_id"].map(fact)
    out = out.merge(cross[["visit_id", "dev_match"]], how="left")
    return out.merge(
        det[["visit_id", "delay_s"]].rename(columns={"delay_s": "dev_det"}), how="left"
    )


def _at_arrival(plan_tr, matcher, m, track, fact) -> pd.DataFrame:
    """Онлайн-оценка текущего отклонения в момент фактического прибытия на каждый визит."""
    tb = plan_tr["tb"].to_numpy(dtype="datetime64[ns]")
    delay = plan_tr["visit_id"].map(fact).to_numpy(dtype=float)
    rows = []
    for k in np.flatnonzero(np.isfinite(delay)):
        t_fact = tb[k] + np.timedelta64(int(delay[k] * 1e9), "ns")
        t_s = t_fact.astype(np.int64) / NS
        dev_m = sched_dev_at(matcher, m, float(t_s))
        det = online_cur_dev(plan_tr, track, pd.Timestamp(t_fact).to_pydatetime())
        rows.append((int(plan_tr["visit_id"].iat[k]), bool(plan_tr["manual_fill"].iat[k]),
                     int(plan_tr["new_trip"].iat[k]), delay[k], dev_m,
                     np.nan if det is None else det))  # fmt: skip
    cols = ["visit_id", "manual_fill", "new_trip", "fact", "dev_match", "dev_det"]
    return pd.DataFrame(rows, columns=cols)


def _compare(df: pd.DataFrame) -> dict:
    """MAE матчинга и детектора: покрытие и ошибка на общих визитах."""

    def block(d: pd.DataFrame) -> dict:
        both = d.dropna(subset=["dev_match", "dev_det"])
        return {
            "n_visits": len(d),
            "coverage_matching": round(float(d["dev_match"].notna().mean()), 4),
            "coverage_detector": round(float(d["dev_det"].notna().mean()), 4),
            "matching_own": _err_stats(
                d["dev_match"].dropna() - d.loc[d["dev_match"].notna(), "fact"]
            ),
            "matching_common": _err_stats(both["dev_match"] - both["fact"]),
            "detector_common": _err_stats(both["dev_det"] - both["fact"]),
        }

    df = df.dropna(subset=["fact"])
    mid = df[(~df["manual_fill"]) & (df["new_trip"] == 0)]
    return {"all": block(df), "manual_fill_false_mid_trip": block(mid)}


def main() -> int:
    plan = load_plan(RAW / "train" / "schedule.csv")
    traffic = load_traffic(RAW / "train" / "traffic.csv")
    plans, tracks = split_by_tr(plan), split_tracks(traffic)
    fact = _fact_delay("train")
    real = sorted((set(tracks) - synthetic_tr_ids(traffic)) & set(plans))
    q, arr, at, lat, matches = [], [], [], [], {}
    for tr_id in real:
        matcher, m, sec = _run_vehicle(plans[tr_id], tracks[tr_id])
        matches[tr_id] = (matcher, m)
        q.append(_quality(matcher, m, tracks[tr_id]))
        arr.append(_arrivals(plans[tr_id], tracks[tr_id], matcher, m, fact))
        at.append(_at_arrival(plans[tr_id], matcher, m, tracks[tr_id], fact))
        lat.append(sec)
    qs = pd.DataFrame(q).sum()
    allm = pd.concat([mm for _, mm in matches.values()], ignore_index=True)
    on = allm[allm["on_route"]]
    report = {
        "vehicles": len(real),
        "on_route_share_in_trip_windows": round(
            qs["on_route_in_windows"] / qs["valid_in_windows"], 4
        ),
        "offset_m": {
            "median": round(float(on["offset_m"].median()), 1),
            "p90": round(float(on["offset_m"].quantile(0.9)), 1),
            "median_all_matched": round(float(allm["offset_m"].median()), 1),
        },
        "monotonic": {
            "steps": int(qs["steps"]),
            "back_steps_tracking": int(qs["back_steps_tracking"]),
            "reacquire_resets": int(qs["reacquire_resets"]),
        },
        "latency_us_per_point": {
            "mean": round(float(np.mean(lat)) * 1e6, 1),
            "max_vehicle": round(float(np.max(lat)) * 1e6, 1),
        },
        "arrival_by_crossing": _compare(pd.concat(arr, ignore_index=True)),
        "deviation_at_arrival_online": _compare(pd.concat(at, ignore_index=True)),
        "sched_dev_gps_feature": feature_report(RAW, plans, tracks, matches),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
