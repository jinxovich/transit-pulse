"""NDTP-кодек: ячейки ``G6Cell*``, кадры NPL/NPH, потоковый декодер.

Публичный API зафиксирован в ``docs/INTERFACES.md`` §1.
"""

from transit_core.ndtp.cells import Cell, NavCell, make_cell
from transit_core.ndtp.codec import (
    Frame,
    ParseError,
    crc16_modbus,
    decode_frame,
    encode_handshake,
    encode_realtime,
    encode_result,
    frame_fields,
)
from transit_core.ndtp.framing import FrameDecoder

__all__ = [
    "Cell",
    "Frame",
    "FrameDecoder",
    "NavCell",
    "ParseError",
    "crc16_modbus",
    "decode_frame",
    "encode_handshake",
    "encode_realtime",
    "encode_result",
    "frame_fields",
    "make_cell",
]
