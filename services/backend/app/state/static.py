"""Статические данные дня: плановое расписание, справочник бортов, маршрутная сеть."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from transit_core import schemas as S
from transit_core.network import build_network, route_id_of
from transit_core.route_line import TripLine, build_lines
from transit_core.segment_speed import Segments, load_history, route_segments, typical_speeds

from ..pipeline.adapters import load_plan
from .timefmt import fmt

NO_DATA_HINT = "положите архив в ./data и выполните data_prep (uv run python scripts/data_prep.py)"
REQUIRED = ("validate/schedule_plan.csv", "validate/traffic.csv")


@dataclass(frozen=True)
class StaticData:
    """Всё, что не меняется за день и нужно для прогнозов и API."""

    plans: dict[int, pd.DataFrame]
    unit_map: dict[int, int]
    network: S.Network
    route_names: dict[str, str]
    segments: dict[int, Segments] = field(default_factory=dict)
    lines: dict[int, list[TripLine]] = field(default_factory=dict)

    def plan_of(self, tr_id: int | None) -> pd.DataFrame | None:
        """План одного ТС (отсортирован по ``tb``) или ``None``."""
        return self.plans.get(tr_id) if tr_id is not None else None

    def lines_of(self, tr_id: int) -> list[TripLine]:
        """Нитки рейсов ТС для map matching (строятся по плану, если не посчитаны заранее)."""
        lines = self.lines.get(tr_id)
        return lines if lines is not None else build_lines(self.plans[tr_id])


def missing_data(data_dir: Path) -> str | None:
    """Понятная ошибка, если данных организаторов нет; иначе ``None``."""
    absent = [p for p in REQUIRED if not (data_dir / p).is_file()]
    if absent:
        return f"Нет файлов {', '.join(absent)} в {data_dir}: {NO_DATA_HINT}"
    return None


def load_unit_map(traffic_csv: Path) -> dict[int, int]:
    """``unit_id → tr_id`` из телеметрии (соответствие 1:1)."""
    t = pd.read_csv(traffic_csv, usecols=["tr_id", "unit_id"]).drop_duplicates()
    return {int(u): int(tr) for u, tr in t[["unit_id", "tr_id"]].itertuples(index=False)}


def load_static(data_dir: Path) -> StaticData:
    """Загружает план validate, справочник бортов и строит сеть."""
    plan = load_plan(data_dir / "validate" / "schedule_plan.csv")
    network = build_network(plan)
    plans = {int(k): g.reset_index(drop=True) for k, g in plan.groupby("tr_id")}
    return StaticData(
        plans=plans,
        unit_map=load_unit_map(data_dir / "validate" / "traffic.csv"),
        network=network,
        route_names={r.route_id: r.name for r in network.routes},
        segments={tr: route_segments(p) for tr, p in plans.items()},
        lines={tr: build_lines(p) for tr, p in plans.items()},
    )


def route_of(static: StaticData, tr_id: int | None) -> tuple[str | None, str | None]:
    """``(route_id, route_name)`` ТС с расписанием."""
    if tr_id is None or tr_id not in static.plans:
        return None, None
    rid = route_id_of(tr_id)
    return rid, static.route_names.get(rid)


def stop_ref(row) -> S.StopRef:
    """Ссылка контракта на плановое прибытие (строка плана INTERFACES §3).

    ``row`` — namedtuple из ``itertuples`` или ``pd.Series`` (у неё ``.name`` — индекс).
    """
    name = row["name"] if isinstance(row, pd.Series) else row.name
    return S.StopRef(
        visit_id=str(int(row.visit_id)),
        stop_key=str(row.stop_key),
        name=str(name),
        lon=float(row.lon),
        lat=float(row.lat),
        planned_at=fmt(row.tb),
    )


def load_typical_speeds(data_dir: Path, static: StaticData) -> dict[str, float]:
    """Типичная скорость перегонов по истории ``train/traffic.csv`` (реальные ТС)."""
    path = data_dir / "train" / "traffic.csv"
    if not path.is_file():
        return {}
    return typical_speeds(static.plans, load_history(path))
