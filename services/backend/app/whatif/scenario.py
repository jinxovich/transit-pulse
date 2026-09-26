"""Сценарий what-if: остановки ТС на ближайший час, прогноз без меры и с мерой.

Признаки считаются тем же as-of путём, что и в проходе прогнозов
(``transit_core.features.point_features`` через адаптер), мера меняет только признаки
отклонения (:mod:`.measures`), оба варианта уходят в ML одним батчем без SHAP.
Если ML недоступен — эвристика ``0.7·cur_dev`` (как в проходе), ``model_mode="fallback"``.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from transit_core import schemas as S
from transit_core.risk import risk_of

from ..ml_client import FALLBACK_VERSION, MlClient, MlItem, fallback_item
from ..pipeline.adapters import online_cur_dev, point_features
from ..pipeline.prepare import HORIZON, LOOKAHEAD, detector_plan
from ..state.static import stop_ref
from ..state.store import track_from_points
from ..state.timefmt import fmt
from .measures import (
    DEV_KEYS,
    Measure,
    applied_minutes,
    apply_measure,
    guard,
    is_after_break,
    measure_of,
    note_for,
)

MAX_STOPS = 30
CHANGE_EPS = 1e-6


@dataclass(frozen=True)
class ScenarioStop:
    """Остановка сценария: строка плана и признаки без меры / с мерой."""

    row: object
    lead_min: float
    in_window: bool
    before: dict
    after: dict


def scenario_stops(
    plan_tr: pd.DataFrame, points: list, t: datetime, m: Measure
) -> list[ScenarioStop]:
    """Остановки ``(t, t+60 мин]`` с признаками as-of ``t`` (выполняется в thread-worker'е)."""
    if not points:
        return []
    track = track_from_points(points)
    cur = online_cur_dev(detector_plan(plan_tr, track), track, t)
    cur = None if cur is None or pd.isna(cur) else float(cur)
    rows = plan_tr[(plan_tr["tb"] > t) & (plan_tr["tb"] <= t + LOOKAHEAD)].head(MAX_STOPS)
    lo, hi = t + HORIZON[0], t + HORIZON[1]
    out = []
    for r in rows.itertuples():
        f = dict(point_features(plan_tr, track, t, int(r.visit_id), cur))
        lead = round((r.tb - t).total_seconds() / 60, 2)
        out.append(ScenarioStop(r, lead, lo < r.tb <= hi, f, apply_measure(m, f)))
    return out


def _cur_dev(features: dict) -> float | None:
    v = features.get("cur_dev")
    return None if v is None or math.isnan(v) else float(v)


async def predict_pairs(
    client: MlClient, stops: Sequence[ScenarioStop]
) -> tuple[list[tuple[MlItem, MlItem]], S.ModelMode, str]:
    """Прогнозы (без меры, с мерой) по каждой остановке одним батчем ML."""
    items = [(f"b:{i}", s.before) for i, s in enumerate(stops)]
    items += [(f"a:{i}", s.after) for i, s in enumerate(stops)]
    ml = await client.predict(items, explain=False)

    def pick(key: str, features: dict) -> MlItem:
        if ml is not None and key in ml:
            return ml[key]
        return fallback_item(key, _cur_dev(features))

    pairs = [(pick(f"b:{i}", s.before), pick(f"a:{i}", s.after)) for i, s in enumerate(stops)]
    full = ml is not None and all(k in ml for k, _ in items)
    if not full:
        return pairs, "fallback", FALLBACK_VERSION
    return pairs, "ml", client.model_version


def _stop(m: Measure, s: ScenarioStop, b: MlItem, a: MlItem) -> S.WhatIfStop:
    ref = stop_ref(s.row)
    d1, p1 = guard(m, (b.delay_s, b.p_late), (a.delay_s, a.p_late))
    return S.WhatIfStop(
        visit_id=ref.visit_id or "", stop_key=ref.stop_key, name=ref.name,
        planned_at=ref.planned_at or "", lead_min=s.lead_min, in_window=s.in_window,
        after_break=is_after_break(s.before),
        predicted_before_s=round(b.delay_s, 1), predicted_after_s=round(d1, 1),
        p_late_before=round(b.p_late, 3), p_late_after=round(p1, 3),
        risk_before=risk_of(b.delay_s, b.p_late), risk_after=risk_of(d1, p1),
    )  # fmt: skip


def pick_target(stops: list[S.WhatIfStop], preferred: Sequence[str | None]) -> S.WhatIfStop:
    """Цель сценария: первая найденная из ``preferred``, иначе первая в окне, иначе ближайшая."""
    by_id = {s.visit_id: s for s in stops}
    for visit_id in preferred:
        if visit_id in by_id:
            return by_id[visit_id]
    return next((s for s in stops if s.in_window), stops[0])


def _changed(stops: Sequence[ScenarioStop]) -> bool:
    """Мера поменяла хотя бы один признак отклонения."""
    for s in stops:
        for k in DEV_KEYS:
            b, a = s.before.get(k), s.after.get(k)
            if b is not None and a is not None and not math.isnan(b) and abs(a - b) > CHANGE_EPS:
                return True
    return False


async def evaluate(
    client: MlClient,
    plan_tr: pd.DataFrame,
    points: list,
    t: datetime,
    req: S.WhatIfRequest,
    current: S.Prediction,
    preferred: Sequence[str | None],
) -> S.WhatIfResult | None:
    """Оценка меры ``req`` для ТС; ``None`` — впереди нет плановых остановок на час."""
    m = measure_of(req)
    stops = await asyncio.to_thread(scenario_stops, plan_tr, points, t, m)
    if not stops:
        return None
    pairs, mode, version = await predict_pairs(client, stops)
    rows = [_stop(m, s, b, a) for s, (b, a) in zip(stops, pairs, strict=True)]
    target = pick_target(rows, preferred)
    changed = _changed(stops)
    return S.WhatIfResult(
        vehicle_id=req.vehicle_id, action=req.action,
        value_min=round(applied_minutes(m, [s.before for s in stops]), 1), title=m.title,
        note=note_for(m, any(r.after_break for r in rows), changed, target.after_break),
        applied=changed,
        generated_at=fmt(t), model_version=version, model_mode=mode, current=current,
        target=target,
        delta_delay_s=round(target.predicted_after_s - target.predicted_before_s, 1),
        delta_p_late=round(target.p_late_after - target.p_late_before, 3), stops=rows,
    )  # fmt: skip
