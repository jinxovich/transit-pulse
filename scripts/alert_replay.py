"""Офлайн-реплей политики алертов на дампе потокового прогона (``stream_dump.pkl``).

Воспроизводит логику ``IncidentBook`` backend'а на сохранённых прогнозах окна
``(T+10, T+15]``: проход по сим-минутам, не больше одного активного инцидента на ТС,
одна цель — один инцидент, открытие на первом визите окна, удовлетворившем политике
``min_streak`` проходов подряд. Закрытие — по фактическому прибытию на цель (факт
``test/schedule.csv``) или по таймауту ``planned + max(pred, 0) + 20 мин``.

Факт используется **только** для закрытия и оценки, прогнозы — честные ответы фолд-моделей
(``h_*``) или финальной модели. Запуск::

    uv run python -m scripts.alert_replay --dump data/eval/stream_dump.pkl
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.stream_dump import read_dump
from transit_core.labels import LATE_S, load_schedule_with_fact
from transit_core.plan import load_plan

RESOLVE_TIMEOUT = timedelta(minutes=20)
PASS_STEP = pd.Timedelta(minutes=1)
MIN_LEAD_MIN = 10.0
MIN_TRIP_RECALL = 0.70
SOURCES = {"honest": ("h_delay", "h_p_late"), "final": ("delay_s", "p_late")}
GRID = {
    "alert_delay_s": (120.0, 150.0, 180.0),
    "alert_p_late": (0.5, 0.6, 0.7),
    "mode": ("or", "and"),
    "min_streak": (1, 2, 3),
}
INC_COLUMNS = ["tr_id", "visit_id", "created", "planned", "timeout_at"]


@dataclass(frozen=True)
class Policy:
    """Политика открытия инцидента."""

    alert_delay_s: float = 120.0
    alert_p_late: float = 0.5
    mode: str = "or"
    min_streak: int = 1
    source: str = "honest"

    def label(self) -> str:
        return (f"delay>{self.alert_delay_s:g} {self.mode} p≥{self.alert_p_late:g}, "
                f"N={self.min_streak}")  # fmt: skip


def signal(preds: pd.DataFrame, policy: Policy) -> np.ndarray:
    """Условие алерта для каждой строки (только визиты окна: ``horizon_ok``)."""
    delay_col, p_col = SOURCES[policy.source]
    by_delay = preds[delay_col].to_numpy(float) > policy.alert_delay_s
    by_p = preds[p_col].to_numpy(float) >= policy.alert_p_late
    hit = by_delay & by_p if policy.mode == "and" else by_delay | by_p
    return hit & preds["horizon_ok"].to_numpy(bool)


def streaks(preds: pd.DataFrame, ok: np.ndarray) -> np.ndarray:
    """Сколько проходов подряд (сим-минут) визит удовлетворял условию, включая текущий."""
    d = pd.DataFrame({"tr": preds["tr_id"].to_numpy(), "v": preds["visit_id"].to_numpy(),
                      "t": pd.to_datetime(preds["T"]).to_numpy(), "ok": ok})  # fmt: skip
    d = d.sort_values(["tr", "v", "t"], kind="stable")
    same = (d["tr"].eq(d["tr"].shift()) & d["v"].eq(d["v"].shift())
            & d["t"].diff().eq(PASS_STEP))  # fmt: skip
    run = (~(same & d["ok"].shift(fill_value=False)) | ~d["ok"]).cumsum()
    count = (d.groupby(run).cumcount() + 1).where(d["ok"], 0)
    return count.sort_index().to_numpy(int)  # индекс d — позиции строк preds


def _timeouts(preds: pd.DataFrame, policy: Policy) -> dict[tuple[int, int], pd.Timestamp]:
    """Таймаут цели: план + max(последний прогноз окна, 0) + 20 мин."""
    delay_col = SOURCES[policy.source][0]
    win = preds[preds["horizon_ok"]].sort_values("T", kind="stable")
    last = win.groupby(["tr_id", "visit_id"]).agg(planned=("planned", "first"),
                                                  pred=(delay_col, "last"))  # fmt: skip
    extra = pd.to_timedelta(last["pred"].clip(lower=0), unit="s") + RESOLVE_TIMEOUT
    return (last["planned"] + extra).to_dict()


def simulate(preds: pd.DataFrame, policy: Policy, fact: pd.DataFrame) -> pd.DataFrame:
    """Инциденты, которые открыла бы политика: ``tr_id, visit_id, created, planned``."""
    preds = preds.reset_index(drop=True).assign(
        T=pd.to_datetime(preds["T"]).to_numpy(), planned=pd.to_datetime(preds["planned_at"])
    )
    ok = signal(preds, policy)
    ready = preds["ready"].to_numpy(bool) if "ready" in preds else True
    eligible = (ok & (streaks(preds, ok) >= policy.min_streak) & ~preds["stale"].to_numpy(bool)
                & ready & (preds["planned"] > preds["T"]).to_numpy())  # fmt: skip
    cand = preds[eligible].sort_values(["T", "tr_id", "planned"], kind="stable")
    arrival = fact.set_index("visit_id")["tf"].to_dict()
    timeout = _timeouts(preds, policy)
    active: dict[int, tuple[pd.Timestamp, pd.Timestamp]] = {}  # ТС → (прибытие, таймаут)
    targets: set[tuple[int, int]] = set()
    rows = []
    for r in cand.itertuples(index=False):
        tr, visit, t = int(r.tr_id), int(r.visit_id), r.T
        if tr in active and _blocks(*active[tr], t):
            continue
        if (tr, visit) in targets:
            continue
        tout = timeout[(tr, visit)]
        active[tr] = (arrival.get(visit, pd.NaT), tout)
        targets.add((tr, visit))
        rows.append((tr, visit, t, r.planned, tout))
    return pd.DataFrame(rows, columns=INC_COLUMNS)


def _blocks(arrived: pd.Timestamp, tout: pd.Timestamp, t: pd.Timestamp) -> bool:
    """Инцидент ещё активен в проход ``t``: ТС не прибыло на цель и таймаут не вышел."""
    return not (pd.notna(arrived) and t >= arrived) and not t > tout


def evaluate(inc: pd.DataFrame, preds: pd.DataFrame, fact: pd.DataFrame,
             minutes: pd.DataFrame | None) -> dict:  # fmt: skip
    """Метрики инцидентов против факта: точность, полнота по рейсам/визитам, упреждение."""
    f = fact.set_index("visit_id")
    delay = inc["visit_id"].map(f["delay_s"])
    known = delay.dropna()
    precision = float((known > LATE_S).mean()) if len(known) else None
    reach = preds.loc[preds["horizon_ok"], ["tr_id", "visit_id"]].drop_duplicates()
    reach = reach.assign(delay=reach["visit_id"].map(f["delay_s"]),
                         trip=reach["visit_id"].map(f["trip"]))  # fmt: skip
    late = reach[reach["delay"] > LATE_S]
    late_visits = set(zip(late["tr_id"], late["visit_id"], strict=True))
    late_trips = set(zip(late["tr_id"], late["trip"], strict=True))
    alerted = set(zip(inc["tr_id"], inc["visit_id"], strict=True))
    alerted_trips = set(zip(inc["tr_id"], inc["visit_id"].map(f["trip"]), strict=True))
    trip_recall = _share(late_trips, alerted_trips)
    lead = (pd.to_datetime(inc["planned"]) - pd.to_datetime(inc["created"])).dt.total_seconds() / 60
    hours = _vehicle_hours(minutes, preds)
    return {
        "n_incidents": len(inc),
        "incidents_with_fact": len(known),
        "alerts_per_vehicle_hour": len(inc) / hours if hours else None,
        "vehicle_hours": hours,
        "precision": precision,
        "trip_recall": trip_recall,
        "visit_recall": _share(late_visits, alerted),
        "n_late_trips": len(late_trips),
        "n_late_visits": len(late_visits),
        "lead_ge_10_share": float((lead >= MIN_LEAD_MIN).mean()) if len(inc) else None,
        "lead_median_min": float(lead.median()) if len(inc) else None,
        "f1": _f1(precision, trip_recall),
    }


def _share(universe: set, hit: set) -> float | None:
    return len(universe & hit) / len(universe) if universe else None


def _f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None:
        return None
    return 2 * p * r / (p + r) if p + r else 0.0


def _vehicle_hours(minutes: pd.DataFrame | None, preds: pd.DataFrame) -> float:
    """ТС·часы, когда можно алертить: минуты ТС с ``ready`` и без ``stale``."""
    if minutes is None:
        return preds[["tr_id", "T"]].drop_duplicates().shape[0] / 60
    return float((minutes["ready"].astype(bool) & ~minutes["stale"].astype(bool)).sum()) / 60


def replay(preds: pd.DataFrame, policy: Policy, fact: pd.DataFrame,
           minutes: pd.DataFrame | None = None) -> dict:  # fmt: skip
    """Реплей политики на дампе: метрики и сами инциденты (ключ ``incidents``)."""
    if minutes is not None and "ready" not in preds:
        ready = minutes[["tr_id", "T", "ready"]].drop_duplicates(["tr_id", "T"])
        preds = preds.merge(ready, on=["tr_id", "T"], how="left")
        preds["ready"] = preds["ready"].fillna(False).astype(bool)
    inc = simulate(preds, policy, fact)
    return evaluate(inc, preds, fact, minutes) | {"policy": asdict(policy), "incidents": inc}


def grid_policies(source: str = "honest") -> list[Policy]:
    """Все политики сетки :data:`GRID`."""
    keys = list(GRID)
    return [Policy(**dict(zip(keys, vals, strict=True)), source=source)
            for vals in itertools.product(*GRID.values())]  # fmt: skip


def select(rows: list[dict], min_recall: float = MIN_TRIP_RECALL) -> tuple[dict, bool]:
    """Лучшая политика: max F1 при полноте по рейсам ≥ ``min_recall`` и упреждении 100% ≥ 10
    мин; при равенстве — меньше алертов на ТС·ч. Второй элемент — выполнено ли ограничение
    по полноте (если ни одна политика его не проходит, берётся max F1 без него)."""
    lead_ok = [r for r in rows if r["lead_ge_10_share"] == 1.0]
    pool = [r for r in lead_ok if (r["trip_recall"] or 0) >= min_recall]
    constrained = bool(pool)
    best = min(pool or lead_ok, key=lambda r: (-(r["f1"] or 0), r["alerts_per_vehicle_hour"]))
    return best, constrained


def load_fact(data_dir: Path) -> pd.DataFrame:
    """Факт дня (``test/schedule.csv``) с номером рейса из плана."""
    fact = load_schedule_with_fact(data_dir / "test" / "schedule.csv")
    trip = load_plan(data_dir / "validate" / "schedule_plan.csv").set_index("visit_id")["trip"]
    return fact.assign(trip=fact["visit_id"].map(trip))


def dump_incidents_check(dump: dict, fact: pd.DataFrame, replayed: pd.DataFrame) -> dict:
    """Инциденты онлайн-прогона из дампа (эталон для реплея): число, точность по факту и
    сколько из них реплей открыл на той же цели и в ту же сим-минуту."""
    inc = dump["incidents"]
    visits = inc["target_stop"].map(lambda s: int(s["visit_id"]))
    delay = visits.map(fact.set_index("visit_id")["delay_s"]).dropna()
    online = dict(zip(zip(inc["tr_id"].astype(int), visits, strict=True),
                      pd.to_datetime(inc["created_at"]), strict=True))  # fmt: skip
    same = [online.get((int(r.tr_id), int(r.visit_id))) for r in replayed.itertuples()]
    return {
        "n_incidents": len(inc),
        "precision": float((delay > LATE_S).mean()),
        "same_target_in_replay": sum(t is not None for t in same),
        "same_target_and_minute": sum(t == r for t, r in zip(same, replayed["created"],
                                                             strict=True)),
    }  # fmt: skip


def _metrics_only(res: dict) -> dict:
    return {k: v for k, v in res.items() if k != "incidents"}


def run(dump_path: Path, data_dir: Path) -> dict:
    """Проверка реплея против онлайн-инцидентов и сетка политик на честных прогнозах."""
    dump = read_dump(dump_path)
    preds, minutes, fact = dump["preds"], dump["minutes"], load_fact(data_dir)
    current = Policy()
    final = replay(preds, Policy(source="final"), fact, minutes)
    check = {
        "online": dump_incidents_check(dump, fact, final["incidents"]),
        "replay_final": _metrics_only(final),
    }
    rows = [_metrics_only(replay(preds, p, fact, minutes)) for p in grid_policies()]
    best, constrained = select(rows)
    return {
        "check": check,
        "current_honest": next(r for r in rows if r["policy"] == asdict(current)),
        "selected": best,
        "selected_meets_recall_floor": constrained,
        "min_trip_recall": MIN_TRIP_RECALL,
        "grid": sorted(rows, key=lambda r: -(r["f1"] or 0)),
    }


def main() -> None:
    from scripts.alert_policy_report import write_report

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", type=Path, default=Path("data/eval/stream_dump.pkl"))
    ap.add_argument("--data-dir", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("docs/perf/alert_policy"))
    args = ap.parse_args()
    res = run(args.dump, args.data_dir)
    args.out.with_suffix(".json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2, default=float), "utf-8"
    )
    write_report(res, args.out.with_suffix(".md"))
    print(json.dumps({k: res[k] for k in ("check", "current_honest", "selected")},
                     ensure_ascii=False, indent=2, default=float))  # fmt: skip


if __name__ == "__main__":
    main()
