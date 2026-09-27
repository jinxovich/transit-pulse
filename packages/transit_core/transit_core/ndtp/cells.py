"""Бинарные раскладки ячеек телематики NDTP (``G6Cell*``).

Раскладки взяты из спецификации эмулятора организаторов (раздел 6) и сверены с
реальными байтами эмулятора (``tests/fixtures/emu.bin``). Все поля little-endian,
структуры packed. Ячейка на проводе: ``[type: u8][number: u8][payload]``.

Ячейки дверей и пассажиропотока (``G6CellCrown03``, ``G6CellIrma04``) в спецификации
описаны только списком полей: размеры и порядок взяты из классов самого эмулятора
(javolution-структуры в ``app.jar`` образа), эталонный кадр ими же собран в
``tests/fixtures/emu_doors.bin``.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Final

COORD_SCALE = 10_000_000
"""Масштаб координат: ``|deg| × 1e7`` в u32."""

BIT_NORTH = 1 << 5
BIT_EAST = 1 << 6
BIT_VALID = 1 << 7
_NAV_LOW_BITS = 0x1F

NAV_TYPE = 0
NAV_NAME = "G6CellNav00"
_NAV = struct.Struct("<IIIBBHHHHHBB")


@dataclass(frozen=True)
class NavCell:
    """Навигационная ячейка ``G6CellNav00`` (type 0, 26 байт).

    Координаты — в градусах со знаком (знак берётся из битов N/E ``extraDop``).
    Невалидная точка терминала — ``valid=False`` и нулевые координаты.
    """

    timestamp: int
    lon: float
    lat: float
    valid: bool
    speed_kmh: int = 0
    speed_max_kmh: int = 0
    course: int = 0
    track_m: int = 0
    altitude_m: int = 0
    nsat: int = 0
    pdop: int = 0
    bat_voltage: int = 0
    flags: int = 0


@dataclass(frozen=True)
class Cell:
    """Произвольная (не навигационная) ячейка: тип, номер и разобранные поля."""

    type: int
    number: int
    name: str
    fields: dict[str, int | float | bool]


@dataclass(frozen=True)
class CellLayout:
    """Описание раскладки ячейки: имя, формат ``struct`` и имена полей по порядку.

    ``bits`` — однобитовые флаги, упакованные в последний байт структуры (поле
    ``names[-1]``) начиная с младшего бита, как их кладёт javolution в little-endian.
    Наружу такой байт виден только как набор bool-полей с именами из ``bits``.
    """

    type: int
    name: str
    fmt: struct.Struct
    names: tuple[str, ...]
    brief: tuple[str, ...]
    bits: tuple[str, ...] = ()

    @property
    def fields(self) -> tuple[str, ...]:
        """Имена полей, как они видны в :attr:`Cell.fields` (флаги вместо их байта)."""
        return self.names[:-1] + self.bits if self.bits else self.names


def _layout(
    type_: int, name: str, spec: str, brief: tuple[str, ...], bits: tuple[str, ...] = ()
) -> CellLayout:
    """Строит :class:`CellLayout` из строки ``"поле:код поле:код"`` (коды ``struct``)."""
    pairs = [item.split(":") for item in spec.split()]
    fmt = struct.Struct("<" + "".join(code for _, code in pairs))
    return CellLayout(type_, name, fmt, tuple(n for n, _ in pairs), brief, bits)


_CAN_AXES = " ".join(f"pressureAxis{i}:H" for i in range(5))
DOORS: Final = range(1, 5)
"""Номера дверей в ячейках пассажиропотока (1…4)."""


def _door_counters(prefix: str) -> str:
    """``"<prefix>_door_in1:B … <prefix>_door_out4:B"`` — счётчики вошло/вышло по дверям."""
    return " ".join(f"{prefix}_door_{way}{d}:B" for way in ("in", "out") for d in DOORS)


def _door_names(prefix: str, kinds: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{prefix}_{kind}{d}" for kind in kinds for d in DOORS)


IRMA_FLAGS: Final = _door_names("irma", ("present_door", "closed_door"))
"""Флаги ``G6CellIrma04``: датчик двери есть (биты 0…3), дверь закрыта (биты 4…7)."""

LAYOUTS: dict[int, CellLayout] = {
    lay.type: lay
    for lay in (
        _layout(
            2,
            "G6CellIntSensor02",
            "an_in0:H an_in1:H an_in2:H an_in3:H di_in:B di_out:B di0_counter:H "
            "di1_counter:H di2_counter:H di3_counter:H odometer:I csq:B gprs_state:B "
            "accel_energy:B ext_volt:b",
            ("odometer", "csq", "ext_volt"),
        ),
        _layout(
            3,
            "G6CellCrown03",
            f"odometer:I zone:H {_door_counters('corona')}",
            _door_names("corona", ("door_in", "door_out")),
        ),
        _layout(
            4,
            "G6CellIrma04",
            f"odometer:I zone:H {_door_counters('irma')} door_flags:B",
            _door_names("irma", ("door_in", "door_out")) + IRMA_FLAGS,
            bits=IRMA_FLAGS,
        ),
        _layout(
            8,
            "G6CellUsi08",
            "det_status:B level_mm:H level_l:H temperature:B",
            ("level_l", "temperature"),
        ),
        _layout(
            10,
            "G6CellCan10",
            "secFlagStatus:I allTimeEngine:I allTrack:I allFuelConsum:I fuelLevel:H "
            f"speedTurnEngine:H tEngine:h speed:B {_CAN_AXES} flagAlarm:I",
            ("speedTurnEngine", "tEngine", "fuelLevel", "speed"),
        ),
        _layout(
            15,
            "G6CellLls15",
            "status:H main_float_level:I temperature_average:I percent_of_volume:I "
            "total_Volume:I weight:I density:I net_Standard_Volume:I level_of_water:I "
            "pressure:I vapor_temperature_average:I vapor_Weight:I liquid_phase_Weight:I",
            ("percent_of_volume", "total_Volume"),
        ),
        _layout(16, "G6CellTermo16", "status:I temp:i", ("temp",)),
    )
}
"""Известные ячейки, кроме навигации: type → раскладка."""

CELL_SIZES: dict[int, int] = {NAV_TYPE: _NAV.size} | {t: lay.fmt.size for t, lay in LAYOUTS.items()}
"""Размер полезной нагрузки ячейки (без заголовка ``type, number``)."""


def _signed(raw: int, positive: bool) -> float:
    """Переводит ``|deg| × 1e7`` в градусы со знаком полушария (``-0.0`` для W/S нуля)."""
    value = raw / COORD_SCALE
    return value if positive else -value


def _abs_raw(deg: float) -> int:
    """Модуль координаты в единицах 1e-7 градуса."""
    return round(abs(deg) * COORD_SCALE)


def _is_positive(deg: float) -> bool:
    """Знак координаты с учётом ``-0.0`` (важно для точного roundtrip)."""
    return math.copysign(1.0, deg) > 0


def decode_nav(payload: bytes) -> NavCell:
    """Разбирает 26 байт ``G6CellNav00``."""
    (ts, lon, lat, dop, bat, spd, spd_max, course, track, alt, nsat, pdop) = _NAV.unpack(payload)
    return NavCell(
        timestamp=ts,
        lon=_signed(lon, bool(dop & BIT_EAST)),
        lat=_signed(lat, bool(dop & BIT_NORTH)),
        valid=bool(dop & BIT_VALID),
        speed_kmh=spd,
        speed_max_kmh=spd_max,
        course=course,
        track_m=track,
        altitude_m=alt,
        nsat=nsat,
        pdop=pdop,
        bat_voltage=bat,
        flags=dop,
    )


def encode_nav(nav: NavCell) -> bytes:
    """Кодирует ``G6CellNav00``: биты N/E/valid берутся из координат и ``valid``.

    Младшие биты ``extraDop`` (тревога, SOS и т.п.) — из ``nav.flags``.
    """
    dop = nav.flags & _NAV_LOW_BITS
    dop |= BIT_NORTH if _is_positive(nav.lat) else 0
    dop |= BIT_EAST if _is_positive(nav.lon) else 0
    dop |= BIT_VALID if nav.valid else 0
    return _NAV.pack(
        nav.timestamp,
        _abs_raw(nav.lon),
        _abs_raw(nav.lat),
        dop,
        nav.bat_voltage,
        nav.speed_kmh,
        nav.speed_max_kmh,
        nav.course,
        nav.track_m,
        nav.altitude_m,
        nav.nsat,
        nav.pdop,
    )


def decode_cell(type_: int, number: int, payload: bytes) -> Cell:
    """Разбирает известную ячейку ``type_`` (кроме навигации)."""
    lay = LAYOUTS[type_]
    values = lay.fmt.unpack(payload)
    fields: dict[str, int | float | bool] = dict(zip(lay.names, values, strict=True))
    if lay.bits:
        packed = values[-1]
        flags = {name: bool(packed >> i & 1) for i, name in enumerate(lay.bits)}
        fields = {k: v for k, v in fields.items() if k != lay.names[-1]} | flags
    return Cell(type_, number, lay.name, fields)


def _pack_bits(lay: CellLayout, fields: dict[str, int | float | bool]) -> int:
    """Собирает байт флагов из bool-полей ``lay.bits`` (младший бит — первый флаг)."""
    return sum(1 << i for i, name in enumerate(lay.bits) if fields.get(name))


def encode_cell(cell: Cell) -> bytes:
    """Кодирует ячейку вместе с заголовком ``[type][number]``; недостающие поля — нули."""
    lay = LAYOUTS[cell.type]
    values = [int(cell.fields.get(name, 0)) for name in lay.names]
    if lay.bits:
        values[-1] = _pack_bits(lay, cell.fields)
    return bytes((cell.type, cell.number)) + lay.fmt.pack(*values)


def make_cell(name: str, number: int = 0, **fields: int | bool) -> Cell:
    """Удобный конструктор ячейки по имени класса (``"G6CellUsi08"`` и т.п.).

    Флаги (``irma_closed_door1`` и т.п.) по умолчанию ``False``, числа — ``0``.
    """
    for lay in LAYOUTS.values():
        if lay.name == name:
            values = {n: fields.get(n, 0) for n in lay.fields}
            values |= {n: bool(fields.get(n, False)) for n in lay.bits}
            return Cell(lay.type, number, name, values)
    raise KeyError(f"неизвестная ячейка {name}")
