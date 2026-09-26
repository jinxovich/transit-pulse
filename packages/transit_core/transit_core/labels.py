"""Генератор целевой разметки (воспроизводит ``labels_train`` / ``labels_test``).

Это **единственный** модуль ядра, который читает ``time_fact_begin``: факт здесь — разметка,
а не признак. Признаки (:mod:`transit_core.features`) получают только план из
:func:`transit_core.plan.load_plan`.

Правило цели: для точки ``(tr_id, T)`` цель — первая остановка ТС с плановым временем в окне
``(T + 10 мин, T + 15 мин]``; ``target_delay_s = факт − план``. Подсказка ``cur_dev_s`` —
задержка на последней остановке с плановым временем ``<= T``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

WINDOW_FROM = pd.Timedelta(minutes=10)
WINDOW_TO = pd.Timedelta(minutes=15)
LATE_S = 120.0
EARLY_S = -60.0
_COLS = ["tt_action_item_id", "tr_id", "time_begin", "time_fact_begin"]


def load_schedule_with_fact(path: Path) -> pd.DataFrame:
    """Расписание с фактом: ``visit_id, tr_id, tb, tf, delay_s``; сортировка по ``(tr_id, tb)``."""
    s = pd.read_csv(path, usecols=_COLS)
    out = pd.DataFrame({
        "visit_id": s["tt_action_item_id"].astype(np.int64),
        "tr_id": s["tr_id"].astype(np.int64),
        "tb": pd.to_datetime(s["time_begin"], format="ISO8601"),
        "tf": pd.to_datetime(s["time_fact_begin"], format="ISO8601"),
    })
    out["delay_s"] = (out["tf"] - out["tb"]).dt.total_seconds()
    return out.sort_values(["tr_id", "tb"], kind="stable").reset_index(drop=True)


def target_class(delay_s: float) -> str:
    """Класс задержки по порогам организаторов (−60 c / +120 c)."""
    if delay_s < EARLY_S:
        return "early"
    return "late" if delay_s > LATE_S else "ontime"


def make_label(sched_tr: pd.DataFrame, t: datetime) -> dict | None:
    """Разметка одной точки ``(ТС, T)``.

    :param sched_tr: расписание одного ТС с фактом (из :func:`load_schedule_with_fact`).
    :param t: момент прогноза.
    :return: ``target_stop_id, target_time_begin, target_delay_s, target_class, cur_dev_s``
        или None, если в окне нет остановок.
    """
    t = pd.Timestamp(t)
    tb = sched_tr["tb"]
    in_window = np.flatnonzero(((tb > t + WINDOW_FROM) & (tb <= t + WINDOW_TO)).to_numpy())
    if len(in_window) == 0:
        return None
    i = int(in_window[0])
    past = np.flatnonzero((tb <= t).to_numpy())
    delay = float(sched_tr["delay_s"].iat[i])
    return {
        "target_stop_id": int(sched_tr["visit_id"].iat[i]),
        "target_time_begin": tb.iat[i],
        "target_delay_s": delay,
        "target_class": target_class(delay),
        "cur_dev_s": float(sched_tr["delay_s"].iat[int(past[-1])]) if len(past) else np.nan,
    }


def make_labels(sched: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Разметка для набора точек ``(tr_id, T)``; строки без цели — NaN."""
    by_tr = {k: g.reset_index(drop=True) for k, g in sched.groupby("tr_id")}
    rows = []
    for r in points.itertuples():
        lab = make_label(by_tr[r.tr_id], pd.Timestamp(r.T)) if r.tr_id in by_tr else None
        rows.append(lab or {})
    return pd.DataFrame(rows, index=points.index)
