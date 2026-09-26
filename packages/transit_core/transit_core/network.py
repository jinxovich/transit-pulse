"""Маршрутная сеть дня из планового расписания: остановки, маршруты, перегоны.

Общий код для бэкенда и генератора моков дашборда (``contracts.mockgen``):
одинаковые ключи остановок и id перегонов на карте и в API.
"""

from __future__ import annotations

import hashlib
from collections import Counter

import pandas as pd

from transit_core import schemas as S

NO_ADDRESS = "Остановка без адреса"
ROUTE_COLORS = (
    "#7C8CF8", "#4FB6C9", "#B98AE6", "#C9A66B", "#6FA8DC", "#E08FB5", "#8FBF9F",
    "#A3A9F5", "#D4A5A5", "#79C2B4", "#C4B5FD", "#9CB4CC", "#E6B89C",
)  # fmt: skip


def stop_key(geom: str) -> str:
    """Стабильный короткий ключ остановки по её WKT-координатам."""
    return "st_" + hashlib.md5(geom.encode()).hexdigest()[:8]


def short_name(address: str) -> str:
    """Адрес без номера дома — для названий маршрутов."""
    return address.split(", д.")[0].split(", вл.")[0]


def route_id_of(tr_id: int | str) -> str:
    """Идентификатор маршрута ТС (один маршрут на ТС за день)."""
    return f"r{tr_id}"


def segment_id_of(route_id: str, from_key: str, to_key: str) -> str:
    """Идентификатор перегона «остановка → остановка» внутри маршрута."""
    return f"{route_id}:{from_key}>{to_key}"


def route_name(g: pd.DataFrame, name_col: str = "name") -> str:
    """«Конечная A ↔ Конечная B» по самым частым концам рейсов ТС."""
    trips = [t for _, t in g.groupby("trip") if len(t) > 5] or [g]
    ends: Counter[str] = Counter()
    for t in trips:
        # Конечные без адреса в названии бесполезны — берём ближайшие к краям с адресом.
        named = t.loc[t[name_col] != NO_ADDRESS, name_col]
        if len(named):
            ends.update([short_name(named.iloc[0]), short_name(named.iloc[-1])])
    top = [name for name, _ in ends.most_common(2)]
    if len(top) == 2:
        return " ↔ ".join(top)
    return f"{top[0]} (кольцевой)" if top else NO_ADDRESS


def _segments(route_id: str, g: pd.DataFrame) -> dict[str, S.NetworkSegment]:
    segments = {}
    for _, trip in g.groupby("trip"):
        pts = list(trip[["stop_key", "lon", "lat"]].itertuples(index=False))
        for a, b in zip(pts, pts[1:], strict=False):
            if a.stop_key == b.stop_key:
                continue
            seg_id = segment_id_of(route_id, a.stop_key, b.stop_key)
            segments[seg_id] = S.NetworkSegment(
                segment_id=seg_id,
                route_id=route_id,
                from_stop_key=a.stop_key,
                to_stop_key=b.stop_key,
                geometry=S.LineString(coordinates=[(a.lon, a.lat), (b.lon, b.lat)]),
            )
    return segments


def build_network(plan: pd.DataFrame, name_col: str = "name") -> S.Network:
    """Остановки, маршруты (по одному на ТС) и перегоны между соседними остановками.

    ``plan`` — плановое расписание с колонками ``tr_id, trip, stop_key, lon, lat``
    и адресом остановки в ``name_col``, отсортированное по ``(tr_id, tb)``.
    """
    stops_df = plan.drop_duplicates("stop_key")
    cols = ["stop_key", name_col, "lon", "lat"]
    stops = [
        S.Stop(stop_key=k, name=n, lon=x, lat=y)
        for k, n, x, y in stops_df[cols].itertuples(index=False)
    ]
    routes, segments = [], {}
    for i, (tr_id, g) in enumerate(plan.groupby("tr_id")):
        route_id = route_id_of(tr_id)
        segments.update(_segments(route_id, g))
        routes.append(
            S.Route(
                route_id=route_id,
                name=route_name(g, name_col),
                color=ROUTE_COLORS[i % len(ROUTE_COLORS)],
                vehicle_ids=[str(tr_id)],
                stop_keys=list(dict.fromkeys(g["stop_key"])),
            )
        )
    bbox = (
        float(stops_df["lon"].min()),
        float(stops_df["lat"].min()),
        float(stops_df["lon"].max()),
        float(stops_df["lat"].max()),
    )
    return S.Network(stops=stops, routes=routes, segments=list(segments.values()), bbox=bbox)
