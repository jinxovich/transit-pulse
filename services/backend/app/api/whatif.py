"""REST: what-if — оценка влияния упреждающей меры на график ТС."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException

from transit_core import schemas as S

from ..whatif.scenario import evaluate
from .deps import Rt, need_static

router = APIRouter(prefix="/api/v1", tags=["What-if"])

_VEHICLE_HINT = (
    "`vehicle_id` — ТС из живого потока: возьмите его из `GET /api/v1/incidents` (ТС с открытым "
    "инцидентом) или `GET /api/v1/vehicles`. 122048 ходит в потоке validate весь день, но если "
    "у него сейчас нет прогноза (прогрев, межрейсовый отстой), ответ будет 409 — подставьте "
    "другое ТС."
)
WHATIF_EXAMPLES = {
    "hold_at_stop": {
        "summary": "Придержать на остановке 2 мин",
        "description": _VEHICLE_HINT,
        "value": {"vehicle_id": "122048", "action": "hold_at_stop", "value": 2},
    },
    "shorten_dwell": {
        "summary": "Сократить отстой на конечной на 3 мин",
        "description": _VEHICLE_HINT,
        "value": {"vehicle_id": "122048", "action": "shorten_dwell", "value": 3},
    },
    "skip_layover": {
        "summary": "Выпустить с конечной без отстоя",
        "description": _VEHICLE_HINT + " `value` для этой меры не нужен.",
        "value": {"vehicle_id": "122048", "action": "skip_layover"},
    },
    "add_reserve": {
        "summary": "Резервный выпуск на рейс после конечной",
        "description": _VEHICLE_HINT + " `value` для этой меры не нужен.",
        "value": {"vehicle_id": "122048", "action": "add_reserve"},
    },
}


@router.post(
    "/whatif", response_model=S.WhatIfResult, summary="Что если: оценка упреждающей меры",
    responses={
        404: {"description": "ТС не найдено"},
        409: {"description": "У ТС нет прогноза (прогрев, нет расписания) или остановок впереди"},
    },
)  # fmt: skip
async def whatif(
    body: Annotated[S.WhatIfRequest, Body(openapi_examples=WHATIF_EXAMPLES)], rt: Rt
) -> S.WhatIfResult:
    """Прогноз ТС без меры и с мерой по остановкам окна (T+10, T+15] и ближайших 60 минут.

    Меры (``action``):

    * ``hold_at_stop`` — придержать на остановке ``value`` мин (по умолчанию 2):
      текущее отклонение сдвигается на +N;
    * ``shorten_dwell`` — сократить отстой на конечной на ``value`` мин (по умолчанию 3):
      −N к отклонению после разрыва рейса (не больше планового отстоя, не раньше графика);
    * ``skip_layover`` — выпустить с конечной без отстоя (весь плановый отстой);
    * ``add_reserve`` — резервный («подменный») выпуск: рейс после конечной идёт по графику.

    Считается теми же признаками (``transit_core.features``) и той же моделью, что и
    основной прогноз; при недоступности ML — эвристика (``model_mode="fallback"``).
    Цель сравнения — остановка открытого инцидента ТС, иначе цель текущего прогноза.
    """
    static = need_static(rt)
    rec = rt.store.vehicles.get(body.vehicle_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"ТС {body.vehicle_id} не найдено")
    if rec.prediction is None or rec.tr_id not in static.plans:
        raise HTTPException(
            status_code=409,
            detail=f"У ТС {body.vehicle_id} пока нет прогноза (прогрев, нет расписания или "
            "данных) — оценить меру нельзя",
        )
    preferred = [rt.book.active_targets().get(rec.vehicle_id), rec.prediction.target_stop.visit_id]
    res = await evaluate(rt.ml, static.plans[rec.tr_id], list(rec.points), rt.sim_now(), body,
                         rec.prediction, preferred)  # fmt: skip
    if res is None:
        raise HTTPException(
            status_code=409, detail=f"У ТС {body.vehicle_id} нет плановых остановок в ближайший час"
        )
    return res
