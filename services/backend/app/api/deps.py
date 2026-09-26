"""Общие зависимости роутеров."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from ..runtime import Runtime
from ..state.static import StaticData


def get_rt(request: Request) -> Runtime:
    """Состояние сервиса из ``app.state``."""
    return request.app.state.rt


Rt = Annotated[Runtime, Depends(get_rt)]
"""Зависимость FastAPI: состояние сервиса."""


def need_static(rt: Runtime) -> StaticData:
    """Статические данные или понятная 503, если датасета нет."""
    if rt.static is None:
        raise HTTPException(status_code=503, detail=rt.data_error or "Данные не загружены")
    return rt.static
