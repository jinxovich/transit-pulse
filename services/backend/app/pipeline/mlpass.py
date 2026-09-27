"""Двухпроходный вызов ML: прогноз для всех ТС, вклады признаков — только где нужна причина.

Точный SHAP CatBoost — почти всё время батча (~100 мс на весь флот), а прогноз без него
считается за единицы миллисекунд. Поэтому:

1. ``explain=false`` — прогноз для всего батча;
2. ``explain=true`` — только для визитов, причину которых видит диспетчер: текущий
   прогноз ТС с риском red/yellow, первый красный визит (кандидат в инцидент) и цель
   открытого инцидента. Зелёным и дальним визитам окна SHAP не нужен.

Если второй вызов не удался, прогнозы первого остаются, а причина считается по правилам
(:func:`transit_core.causes.infer_cause` без вкладов).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace

from transit_core.risk import risk_of

from ..ml_client import MlClient, MlItem
from .assemble import all_visits, ml_items
from .prepare import VehicleTask

log = logging.getLogger(__name__)
EXPLAIN_RISKS = ("red", "yellow")
"""Уровни риска, для которых запрашиваются вклады признаков."""


@dataclass(frozen=True)
class MlPass:
    """Результат ML в проходе: прогнозы и латентности двух вызовов."""

    items: dict[str, MlItem] | None
    predict_ms: float | None = None
    explain_ms: float | None = None
    explained: int = 0


def _needs_explain(task: VehicleTask, ml: dict[str, MlItem], target: str | None) -> list:
    """Визиты ТС, чья причина видна диспетчеру: первый (состояние ТС) при риске
    red/yellow, первый красный (кандидат в инцидент) и цель активного инцидента
    (в окне или follow-up)."""
    visits = [v for v in task.visits if v.item_id in ml]
    risk = {v.item_id: risk_of(ml[v.item_id].delay_s, ml[v.item_id].p_late) for v in visits}
    chosen = [v for v in visits[:1] if risk[v.item_id] in EXPLAIN_RISKS]
    chosen += [v for v in visits if risk[v.item_id] == "red"][:1]
    chosen += [v for v in all_visits(task) if v.visit_id == target and v.item_id in ml]
    return list({v.item_id: v for v in chosen}.values())


def explain_items(
    tasks: list[VehicleTask], ml: dict[str, MlItem], targets: Mapping[str, str]
) -> list[tuple[str, dict]]:
    """Визиты для вкладов признаков; ``targets`` — ТС → визит-цель активного инцидента."""
    out = []
    for task in tasks:
        if not task.stale:
            visits = _needs_explain(task, ml, targets.get(task.vehicle_id))
            out += [(v.item_id, v.features) for v in visits]
    return out


def with_contributions(ml: dict[str, MlItem], explained: dict[str, MlItem]) -> dict[str, MlItem]:
    """Новые прогнозы: числа — из первого вызова, вклады — из второго."""
    return {
        k: replace(v, contributions=explained[k].contributions) if k in explained else v
        for k, v in ml.items()
    }


async def run_ml(client: MlClient, tasks: list[VehicleTask], targets: Mapping[str, str]) -> MlPass:
    """Прогноз для всех свежих ТС и вклады признаков только для рискованных."""
    items = ml_items(tasks)
    ml = await client.predict(items, explain=False) if items else None
    if ml is None:
        return MlPass(None)
    predict_ms = client.last_latency_ms
    hot = explain_items(tasks, ml, targets)
    if not hot:
        return MlPass(ml, predict_ms)
    extra = await client.predict(hot, explain=True)
    if extra is None:
        log.warning("Вклады признаков недоступны (%d визитов): причина по правилам", len(hot))
        return MlPass(ml, predict_ms)
    return MlPass(with_contributions(ml, extra), predict_ms, client.last_latency_ms, len(hot))
