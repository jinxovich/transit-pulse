"""Тонкий адаптер к функциям ``transit_core`` ветки ML (INTERFACES §3).

Бэкенд обращается к расписанию, признакам и стоп-детектору только через этот модуль:
офлайн-обучение и поток используют один и тот же as-of код.
"""

from __future__ import annotations

from transit_core.features import FEATURES, point_features
from transit_core.plan import load_plan
from transit_core.stops_detector import detect_arrivals, online_cur_dev

IMPL: dict[str, str] = {
    "plan": "transit_core.plan",
    "features": f"transit_core.features ({len(FEATURES)})",
    "stops_detector": "transit_core.stops_detector",
}

__all__ = [
    "FEATURES",
    "IMPL",
    "detect_arrivals",
    "load_plan",
    "online_cur_dev",
    "point_features",
]
