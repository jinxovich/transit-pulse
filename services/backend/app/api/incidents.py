"""REST: инциденты и реакция диспетчера."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from transit_core import schemas as S

from .deps import Rt

router = APIRouter(prefix="/api/v1", tags=["Инциденты"])
STATUSES = {"open", "ack", "resolved"}


@router.get("/incidents", response_model=list[S.Incident], summary="Инциденты сессии")
async def incidents(
    rt: Rt,
    status: str | None = Query(None, description="Фильтр через запятую: open,ack,resolved"),
) -> list[S.Incident]:
    """Все инциденты текущей сессии, опционально по статусам."""
    wanted = {s.strip() for s in status.split(",") if s.strip()} if status else None
    if wanted and not wanted <= STATUSES:
        raise HTTPException(status_code=422, detail=f"Неизвестный статус: {wanted - STATUSES}")
    items = rt.book.incidents.values()
    return [i for i in items if wanted is None or i.status in wanted]


@router.get(
    "/incidents/{incident_id}", response_model=S.Incident, summary="Карточка инцидента",
    responses={404: {"description": "Инцидент не найден"}},
)  # fmt: skip
async def incident(incident_id: str, rt: Rt) -> S.Incident:
    """Инцидент по id: прогноз, причина с обоснованием, перегон, рекомендации."""
    inc = rt.book.incidents.get(incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail=f"Инцидент {incident_id} не найден")
    return inc


@router.post(
    "/incidents/{incident_id}/ack", response_model=S.Incident, summary="Реакция диспетчера",
    responses={404: {"description": "Инцидент не найден"}},
)  # fmt: skip
async def ack(incident_id: str, rt: Rt, body: S.AckRequest | None = None) -> S.Incident:
    """Отмечает инцидент принятым (``ack``) с выбранным действием; уходит в WS сразу."""
    inc = rt.ack(incident_id, body or S.AckRequest())
    if inc is None:
        raise HTTPException(status_code=404, detail=f"Инцидент {incident_id} не найден")
    return inc
