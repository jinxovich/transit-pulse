"""Снимок дверей/пассажиропотока из ячеек NDTP ``G6CellIrma04`` и ``G6CellCrown03``.

В датасете и в режиме autoGenerate эмулятора этих ячеек нет: снимок появляется, только
если терминал их прислал, и в признаки модели не идёт — это телематика для диспетчера
(ТЗ: «сопоставление координат, скорости и статуса дверей»).

IRMA приоритетнее «Короны»: у неё есть статус дверей. Если в пакете несколько
экземпляров ячейки (``number`` 0, 1, …), берётся первый — номера дверей у разных
экземпляров несопоставимы.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from transit_core import schemas as S

from ..state.timefmt import fmt
from .codec import Cell

IRMA = "G6CellIrma04"
CROWN = "G6CellCrown03"
DOORS = range(1, 5)
_SOURCE: dict[str, S.DoorSource] = {IRMA: "irma", CROWN: "corona"}
"""Имя ячейки → источник снимка; он же префикс полей (``irma_door_in1``, ``corona_door_in1``)."""


def _first(cells: Iterable[Cell], name: str) -> Cell | None:
    return next((c for c in cells if c.name == name), None)


def _counter(cell: Cell, prefix: str, way: str) -> int:
    return sum(int(cell.fields.get(f"{prefix}_door_{way}{d}", 0)) for d in DOORS)


def _open_doors(cell: Cell) -> list[int]:
    """IRMA: дверь открыта, если датчик на ней есть и флаг «закрыта» снят."""
    f = cell.fields
    return [
        d for d in DOORS if f.get(f"irma_present_door{d}") and not f.get(f"irma_closed_door{d}")
    ]


def door_snapshot(cells: Iterable[Cell], et: datetime) -> S.DoorSnapshot | None:
    """Снимок из ячеек пакета; ``None`` — ячеек дверей в пакете нет."""
    seq = tuple(cells)
    cell = _first(seq, IRMA) or _first(seq, CROWN)
    if cell is None:
        return None
    source = _SOURCE[cell.name]
    has_status = cell.name == IRMA
    open_doors = _open_doors(cell) if has_status else []
    return S.DoorSnapshot(
        at=fmt(et),
        source=source,
        any_open=bool(open_doors) if has_status else None,
        open_doors=open_doors,
        entered=_counter(cell, source, "in"),
        exited=_counter(cell, source, "out"),
    )
