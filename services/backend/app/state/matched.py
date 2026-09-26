"""Положение ТС на нитке графика по map matching — для ``VehicleState``.

Если привязка свежая и уверенная, карта показывает:

* ``current_dev_s`` — GPS-отклонение от нитки графика между остановками
  (:func:`transit_core.matching.schedule_deviation_s`) вместо задержки на последней
  найденной детектором остановке — оно обновляется непрерывно, а не раз в перегон;
* ``next_stop`` — следующая остановка по положению на нитке, а не по плановому времени.

Признак ``cur_dev`` для ML не меняется: модели обучены на онлайн-детекторе.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from transit_core.matching import schedule_deviation_s, to_seconds

from .static import StaticData
from .store import VehicleRecord

MATCH_FRESH_S = 120.0
MIN_CONFIDENCE = 0.5


@dataclass(frozen=True)
class MatchedNow:
    """Отклонение от графика и следующая остановка (строка плана) по матчингу."""

    dev_s: float
    next_row: pd.Series | None


def matched_now(static: StaticData, rec: VehicleRecord, t: datetime) -> MatchedNow | None:
    """Свежая уверенная привязка ТС на момент ``t`` или ``None`` (тогда — как раньше).

    Отклонение считается на момент последней привязки: без новых точек оно не растёт.
    """
    m, matcher, plan_tr = rec.match, rec.matcher, static.plan_of(rec.tr_id)
    if m is None or matcher is None or plan_tr is None:
        return None
    if not m.on_route or m.confidence < MIN_CONFIDENCE or to_seconds(t) - m.t_s > MATCH_FRESH_S:
        return None
    line = matcher.line_of(m.trip)
    if line is None:
        return None
    next_row = plan_tr.iloc[m.next_stop] if m.next_stop is not None else None
    return MatchedNow(schedule_deviation_s(m, line, m.t_s), next_row)
