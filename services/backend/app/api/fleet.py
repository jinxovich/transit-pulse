"""REST: маршрутная сеть, ТС и риск по перегонам."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from transit_core import schemas as S

from ..state.views import segments_risk, vehicle_detail
from .deps import Rt, need_static

router = APIRouter(prefix="/api/v1", tags=["Карта и ТС"])


@router.get("/network", response_model=S.Network, summary="Маршрутная сеть дня")
async def network(rt: Rt) -> S.Network:
    """Остановки, маршруты (по одному на ТС) и перегоны с геометрией, bbox."""
    return need_static(rt).network


@router.get("/vehicles", response_model=list[S.VehicleState], summary="ТС на карте")
async def vehicles(rt: Rt) -> list[S.VehicleState]:
    """Текущее состояние всех ТС (запасной путь к WS)."""
    need_static(rt)
    return list(rt.vehicle_states().values())


@router.get(
    "/vehicles/{vehicle_id}", response_model=S.VehicleDetail, summary="Детали ТС",
    responses={404: {"description": "ТС не найдено"}},
)  # fmt: skip
async def vehicle(vehicle_id: str, rt: Rt) -> S.VehicleDetail:
    """«Нитка графика» ±60 мин (план / факт по GPS / прогноз), ряд отклонения, прогноз."""
    static = need_static(rt)
    rec = rt.store.vehicles.get(vehicle_id)
    state = rt.vehicle_states().get(vehicle_id)
    if rec is None or state is None:
        raise HTTPException(status_code=404, detail=f"ТС {vehicle_id} не найдено")
    incidents = list(rt.book.incidents.values())
    return vehicle_detail(static, rec, state, rt.sim_now(), incidents)


@router.get("/segments/risk", response_model=list[S.SegmentRisk], summary="Риск по перегонам")
async def segments(rt: Rt) -> list[S.SegmentRisk]:
    """Окраска перегонов к целевым остановкам: максимальный риск ТС на перегоне."""
    static = need_static(rt)
    current = {vid: rec.segment for vid, rec in rt.store.vehicles.items() if rec.segment}
    return segments_risk(static, list(rt.vehicle_states().values()), current)
