"""Перегон «предыдущая остановка → целевая» для карточки инцидента и окраски карты."""

from __future__ import annotations

import pandas as pd

from transit_core import schemas as S
from transit_core.network import route_id_of, segment_id_of

from ..state.static import stop_ref


def target_segment(plan_tr: pd.DataFrame, tr_id: int, target: S.StopRef) -> S.Segment | None:
    """Перегон к целевой остановке по плану ТС; ``None`` для первой остановки дня."""
    idx = plan_tr.index[plan_tr["visit_id"].astype(str) == target.visit_id]
    if not len(idx) or idx[0] == 0:
        return None
    prev, cur = plan_tr.iloc[idx[0] - 1], plan_tr.iloc[idx[0]]
    same_trip = prev["trip"] == cur["trip"] and prev["stop_key"] != cur["stop_key"]
    seg_id = segment_id_of(route_id_of(tr_id), prev["stop_key"], cur["stop_key"])
    return S.Segment(
        segment_id=seg_id if same_trip else None,
        from_stop=stop_ref(prev),
        to_stop=target,
        geometry=S.LineString(
            coordinates=[(float(prev["lon"]), float(prev["lat"])),
                         (float(cur["lon"]), float(cur["lat"]))]
        ),
    )  # fmt: skip
