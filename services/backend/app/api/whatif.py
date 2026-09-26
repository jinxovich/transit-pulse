"""REST: what-if — оценка влияния упреждающей меры на график ТС."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from transit_core import schemas as S

from ..whatif.scenario import evaluate
from .deps import Rt, need_static

router = APIRouter(prefix="/api/v1", tags=["What-if"])


@router.post(
    "/whatif", response_model=S.WhatIfResult, summary="Что если: оценка упреждающей меры",
    responses={
        404: {"description": "ТС не найдено"},
        409: {"description": "У ТС нет прогноза (прогрев, нет расписания) или остановок впереди"},
    },
)  # fmt: skip
async def whatif(body: S.WhatIfRequest, rt: Rt) -> S.WhatIfResult:
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
