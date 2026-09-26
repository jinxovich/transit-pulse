"""Точка импорта NDTP-кодека для бэкенда (``transit_core.ndtp``, INTERFACES §1)."""

from __future__ import annotations

from transit_core.ndtp import (
    Cell,
    Frame,
    FrameDecoder,
    NavCell,
    encode_handshake,
    encode_realtime,
    encode_result,
    frame_fields,
)

NPH_CONN_REQUEST = 100
"""NPH-тип handshake терминала (``NPH_SGC_CONN_REQUEST``)."""

__all__ = [
    "NPH_CONN_REQUEST",
    "Cell",
    "Frame",
    "FrameDecoder",
    "NavCell",
    "encode_handshake",
    "encode_realtime",
    "encode_result",
    "frame_fields",
]
