"""Маршрутная сеть дня для моков — общий код в :mod:`transit_core.network`."""

from __future__ import annotations

import pandas as pd

from transit_core import network as _net
from transit_core import schemas as S

ROUTE_COLORS = _net.ROUTE_COLORS


def route_name(g: pd.DataFrame) -> str:
    """«Конечная A ↔ Конечная B» по адресам расписания (колонка ``building_address``)."""
    return _net.route_name(g, "building_address")


def build_network(plan: pd.DataFrame) -> S.Network:
    """Остановки, маршруты и перегоны по плану моков."""
    return _net.build_network(plan, "building_address")
