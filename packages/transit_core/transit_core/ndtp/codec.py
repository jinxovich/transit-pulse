"""Кадры NDTP: ``[NPL 15 байт][NPH 10 байт][тело]``, little-endian.

CRC-16/Modbus (poly ``0xA001``, init ``0xFFFF``) считается по NPH + телу и кладётся в
NPL со свапнутыми байтами (то есть big-endian). Проверено на реальных байтах
эмулятора — см. ``tests/test_ndtp_emulator.py``.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from transit_core.ndtp.cells import (
    CELL_SIZES,
    LAYOUTS,
    NAV_NAME,
    NAV_TYPE,
    Cell,
    NavCell,
    decode_cell,
    decode_nav,
    encode_cell,
    encode_nav,
)

SIGNATURE = 0x7E7E
SIGNATURE_BYTES = b"\x7e\x7e"
NPL_TYPE_NPH = 0x02
NPL = struct.Struct("<HHHHBIH")  # signature, dataSize, flags, crc(см. ниже), type, peer, reqId
NPH = struct.Struct("<HHHI")  # serviceId, type, flags, requestId
NPL_LEN = NPL.size
NPH_LEN = NPH.size
HEADER_LEN = NPL_LEN + NPH_LEN

SERVICE_GENERIC = 0
SERVICE_NAVDATA = 1
NPH_RESULT = 0
NPH_CONN_REQUEST = 100
NPH_REALTIME = 101
NPH_FLAG_REQUEST = 1

HANDSHAKE = struct.Struct("<HHHIII")
PROTO_VERSION = (6, 2)
MAX_PACKET_SIZE = 65535
REQUEST_ID_MASK = 0xFFFF_FFFF


@dataclass(frozen=True)
class Frame:
    """Разобранный кадр NDTP.

    ``cells`` — все ячейки, кроме первой навигационной (она в ``nav``), в порядке
    следования; поэтому ``encode_realtime(unit_id, request_id, nav, cells)``
    воспроизводит исходные байты.
    """

    unit_id: int
    service_id: int
    nph_type: int
    request_id: int
    crc_ok: bool
    nav: NavCell | None
    cells: tuple[Cell, ...]
    handshake: dict[str, int] | None
    raw: bytes


class ParseError(ValueError):
    """Тело кадра не разбирается (обрезанная ячейка, неверная длина handshake)."""


def crc16_modbus(data: bytes) -> int:
    """CRC-16/Modbus: poly ``0xA001`` (отражённый ``0x8005``), init ``0xFFFF``."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _swap16(value: int) -> int:
    """Переставляет байты u16 (так CRC лежит в NPL)."""
    return ((value & 0xFF) << 8) | (value >> 8)


def build_frame(
    unit_id: int, service_id: int, nph_type: int, request_id: int, body: bytes, nph_flags: int
) -> bytes:
    """Собирает кадр: NPL (с CRC) + NPH + тело."""
    nph = NPH.pack(service_id, nph_type, nph_flags, request_id & REQUEST_ID_MASK) + body
    crc = _swap16(crc16_modbus(nph))
    return NPL.pack(SIGNATURE, len(nph), 0, crc, NPL_TYPE_NPH, unit_id, 0) + nph


def encode_handshake(unit_id: int, request_id: int) -> bytes:
    """Кадр ``NPH_SGC_CONN_REQUEST`` (service 0, type 100, тело 18 байт)."""
    body = HANDSHAKE.pack(*PROTO_VERSION, 0, unit_id, MAX_PACKET_SIZE, 0)
    return build_frame(
        unit_id, SERVICE_GENERIC, NPH_CONN_REQUEST, request_id, body, NPH_FLAG_REQUEST
    )


def encode_realtime(
    unit_id: int, request_id: int, nav: NavCell, extra: Sequence[Cell] = ()
) -> bytes:
    """Кадр ``NPH_SND_REALTIME``: ``G6CellNav00`` первой, затем ``extra`` по порядку."""
    body = bytes((NAV_TYPE, 0)) + encode_nav(nav) + b"".join(encode_cell(c) for c in extra)
    return build_frame(unit_id, SERVICE_NAVDATA, NPH_REALTIME, request_id, body, NPH_FLAG_REQUEST)


def encode_result(frame: Frame, code: int = 0) -> bytes:
    """Ответ сервера ``NPH_RESULT`` (service 0, type 0, тело — u32 код ошибки).

    ``requestId`` совпадает с запросом; эмулятор ответ не разбирает, но терминалы ждут.
    """
    body = struct.pack("<I", code)
    return build_frame(frame.unit_id, SERVICE_GENERIC, NPH_RESULT, frame.request_id, body, 0)


def _decode_handshake(body: bytes) -> dict[str, int]:
    """Тело handshake → dict; неверная длина — :class:`ParseError`."""
    if len(body) != HANDSHAKE.size:
        raise ParseError(f"handshake: {len(body)} байт вместо {HANDSHAKE.size}")
    high, low, flags, peer, max_size, _ = HANDSHAKE.unpack(body)
    return {
        "proto_version_high": high,
        "proto_version_low": low,
        "flags": flags,
        "peer_address": peer,
        "max_packet_size": max_size,
    }


def decode_cells(body: bytes) -> tuple[NavCell | None, tuple[Cell, ...], bool]:
    """Разбирает последовательность ячеек realtime-тела.

    Неизвестный тип — прекращаем разбор (без ошибки), уже разобранное сохраняем.
    Возвращает ``(nav, cells, truncated)``; ``truncated`` — ячейка обрезана.
    """
    nav: NavCell | None = None
    cells: list[Cell] = []
    pos = 0
    while pos + 2 <= len(body):
        type_, number = body[pos], body[pos + 1]
        size = CELL_SIZES.get(type_)
        if size is None:
            break
        payload = body[pos + 2 : pos + 2 + size]
        if len(payload) < size:
            return nav, tuple(cells), True
        pos += 2 + size
        if type_ == NAV_TYPE and nav is None:
            nav = decode_nav(payload)
        elif type_ in LAYOUTS:
            cells.append(decode_cell(type_, number, payload))
    return nav, tuple(cells), False


def decode_frame(raw: bytes) -> Frame:
    """Разбирает ровно один целый кадр.

    Битый CRC не мешает разбору (``crc_ok=False``). Обрезанная ячейка или
    неверный handshake — :class:`ParseError`.
    """
    _, size, _, crc_swapped, _, unit_id, _ = NPL.unpack_from(raw)
    service_id, nph_type, _, request_id = NPH.unpack_from(raw, NPL_LEN)
    body = raw[HEADER_LEN : NPL_LEN + size]
    crc_ok = _swap16(crc16_modbus(raw[NPL_LEN : NPL_LEN + size])) == crc_swapped
    nav, cells, handshake = None, (), None
    if (service_id, nph_type) == (SERVICE_GENERIC, NPH_CONN_REQUEST):
        handshake = _decode_handshake(body)
    elif (service_id, nph_type) == (SERVICE_NAVDATA, NPH_REALTIME):
        nav, cells, truncated = decode_cells(body)
        if truncated:
            raise ParseError("realtime: обрезанная ячейка")
    return Frame(unit_id, service_id, nph_type, request_id, crc_ok, nav, cells, handshake, raw)


def _short(name: str) -> str:
    """``G6CellUsi08`` → ``usi08``: префикс полей витрины."""
    return name.removeprefix("G6Cell").lower()


def frame_fields(frame: Frame) -> dict[str, float | int | bool | str]:
    """Плоский dict для витрины «последний пакет» (``LastPacket.fields``)."""
    out: dict[str, Any] = {"unit_id": frame.unit_id, "crc_ok": frame.crc_ok}
    if frame.handshake is not None:
        hs = frame.handshake
        proto = f"{hs['proto_version_high']}.{hs['proto_version_low']}"
        return {"cell": "handshake", **out, "proto": proto}
    nav = frame.nav
    if nav is None:
        return {"cell": f"nph{frame.service_id}.{frame.nph_type}", **out}
    out = {"cell": NAV_NAME, **out, "timestamp": nav.timestamp}
    out |= {"lon": round(nav.lon, 7), "lat": round(nav.lat, 7), "valid": nav.valid}
    out |= {"speed_kmh": nav.speed_kmh, "course": nav.course, "nsat": nav.nsat}
    for cell in frame.cells:
        prefix = f"{_short(cell.name)}_{cell.number}"
        for key in LAYOUTS[cell.type].brief:
            out[f"{prefix}.{key}"] = cell.fields[key]
    return out
