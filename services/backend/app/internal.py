"""Внутренний API для replayer'а: ``POST /internal/sim/session`` (INTERFACES §2)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from transit_core import schemas as S

from .api.deps import Rt
from .state.clock import SessionUpdate
from .state.timefmt import parse

router = APIRouter(prefix="/internal", tags=["Внутреннее"])


class SessionIn(BaseModel):
    """Состояние сессии воспроизведения от replayer'а."""

    session_id: str = Field(min_length=1, max_length=128)
    sim_time: S.NaiveTime
    speed: float = Field(gt=0)
    state: Literal["running", "paused", "stopped"]
    warmup_until: S.NaiveTime | None = None


class SessionOut(BaseModel):
    """Ответ: принято ли как новая сессия и текущие часы."""

    new_session: bool
    clock: S.SimClock


@router.post("/sim/session", response_model=SessionOut, summary="Сессия воспроизведения")
async def sim_session(body: SessionIn, rt: Rt) -> SessionOut:
    """Новый ``session_id`` очищает state и инциденты и рассылает WS ``snapshot``;
    тот же ``session_id`` — только обновление скорости/состояния часов."""
    upd = SessionUpdate(
        session_id=body.session_id,
        sim_time=parse(body.sim_time),
        speed=body.speed,
        state=body.state,
        warmup_until=parse(body.warmup_until) if body.warmup_until else None,
    )
    is_new = rt.apply_session(upd)
    return SessionOut(new_session=is_new, clock=rt.clock.contract(rt.wall()))
