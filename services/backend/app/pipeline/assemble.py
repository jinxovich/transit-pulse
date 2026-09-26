"""Сборка контрактных прогнозов из ответа ML (или эвристики) и признаков."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from transit_core import schemas as S
from transit_core.causes import infer_cause
from transit_core.risk import risk_of

from ..ml_client import FALLBACK_VERSION, MlItem, fallback_item
from ..state.static import stop_ref
from ..state.timefmt import fmt
from .prepare import VehicleTask, VisitTask


@dataclass(frozen=True)
class VisitPrediction:
    """Прогноз по визиту + всё, что нужно инцидентам (причина, рейс)."""

    visit: VisitTask
    prediction: S.Prediction
    cause: S.Cause


def ml_items(tasks: list[VehicleTask]) -> list[tuple[str, dict]]:
    """Один батч в ml: все визиты всех «свежих» ТС."""
    return [(v.item_id, v.features) for t in tasks if not t.stale for v in t.visits]


def _item_for(task: VehicleTask, v: VisitTask, ml: dict[str, MlItem] | None) -> MlItem:
    if ml is not None and not task.stale and v.item_id in ml:
        return ml[v.item_id]
    return fallback_item(v.item_id, task.cur_dev)


def build_prediction(
    task: VehicleTask,
    v: VisitTask,
    item: MlItem,
    t: datetime,
    mode: S.ModelMode,
    version: str,
) -> VisitPrediction:
    """Прогноз контракта для визита ``v`` на сим-минуту ``t``."""
    features = {**v.features, **task.extras()}
    cause = infer_cause(features, item.contributions, item.delay_s, task.stale)
    pred = S.Prediction(
        target_stop=stop_ref(v.row),
        predicted_delay_s=round(item.delay_s, 1),
        p_late=round(item.p_late, 3),
        interval_s=(round(item.q10, 1), round(item.q90, 1)),
        expected_abs_error_s=round(item.expected_abs_error_s, 1),
        lead_min=v.lead_min,
        generated_at=fmt(t),
        horizon_ok=task.horizon_ok,
        model_version=version,
        model_mode=mode,
        risk_level=risk_of(item.delay_s, item.p_late),
        cause_code=cause.code,
    )
    return VisitPrediction(v, pred, cause)


def assemble(
    task: VehicleTask, ml: dict[str, MlItem] | None, t: datetime, ml_version: str
) -> list[VisitPrediction]:
    """Прогнозы по всем визитам ТС в порядке планового времени."""
    out = []
    for v in task.visits:
        use_ml = ml is not None and not task.stale and v.item_id in ml
        mode: S.ModelMode = "ml" if use_ml else "fallback"
        version = ml_version if use_ml else FALLBACK_VERSION
        out.append(build_prediction(task, v, _item_for(task, v, ml), t, mode, version))
    return out
