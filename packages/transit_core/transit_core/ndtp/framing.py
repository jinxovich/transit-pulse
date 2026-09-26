"""Потоковый фрейминг NDTP поверх TCP: частичные/склеенные кадры, мусор, ресинк."""

from __future__ import annotations

import struct

from transit_core.ndtp.codec import (
    NPH_LEN,
    NPL_LEN,
    NPL_TYPE_NPH,
    SIGNATURE_BYTES,
    Frame,
    ParseError,
    decode_frame,
)

_SIZE = struct.Struct("<H")
MAX_DATA_SIZE = 65535
"""Верхняя граница ``dataSize`` (NPH + тело); больше — считаем заголовок ложным."""


class FrameDecoder:
    """Инкрементальный декодер одного TCP-потока.

    ``feed`` принимает очередной кусок байт и возвращает все кадры, которые стали
    полными. Кадры с битым CRC отдаются с ``crc_ok=False`` и считаются в
    ``crc_errors``. Ложный заголовок (``dataSize`` вне ``[10, max_data_size]``,
    NPL-type ≠ 2) и мусор между кадрами — ресинк по ``0x7E7E`` (``resyncs``).
    Кадр с неразбираемым телом — ``parse_errors``, кадр пропускается.
    """

    def __init__(self, max_data_size: int = MAX_DATA_SIZE) -> None:
        self.max_data_size = max_data_size
        self.crc_errors = 0
        self.parse_errors = 0
        self.resyncs = 0
        self.frames = 0
        self._buf = bytearray()

    @property
    def pending(self) -> int:
        """Сколько байт ждут продолжения кадра."""
        return len(self._buf)

    def feed(self, data: bytes) -> list[Frame]:
        """Добавляет байты потока и возвращает готовые кадры по порядку."""
        self._buf += data
        out: list[Frame] = []
        while True:
            raw = self._next_raw()
            if raw is None:
                return out
            frame = self._decode(raw)
            if frame is not None:
                out.append(frame)

    def _skip_to_signature(self, start: int) -> bool:
        """Отбрасывает байты до сигнатуры (начиная с ``start``); False — сигнатуры нет."""
        idx = self._buf.find(SIGNATURE_BYTES, start)
        if idx < 0:
            keep = 1 if self._buf.endswith(SIGNATURE_BYTES[:1]) else 0
            if len(self._buf) > keep:
                self.resyncs += 1
                del self._buf[: len(self._buf) - keep]
            return False
        if idx > 0:
            self.resyncs += 1
            del self._buf[:idx]
        return True

    def _header_ok(self) -> bool:
        """Правдоподобен ли NPL-заголовок в начале буфера."""
        (size,) = _SIZE.unpack_from(self._buf, 2)
        return NPH_LEN <= size <= self.max_data_size and self._buf[8] == NPL_TYPE_NPH

    def _next_raw(self) -> bytes | None:
        """Вырезает из буфера следующий целый кадр или None, если данных мало."""
        while self._skip_to_signature(0):
            if len(self._buf) < NPL_LEN:
                return None
            if not self._header_ok():
                self.resyncs += 1
                del self._buf[:1]
                continue
            (size,) = _SIZE.unpack_from(self._buf, 2)
            total = NPL_LEN + size
            if len(self._buf) < total:
                return None
            raw = bytes(self._buf[:total])
            del self._buf[:total]
            return raw
        return None

    def _decode(self, raw: bytes) -> Frame | None:
        """Разбирает вырезанный кадр, обновляя счётчики."""
        try:
            frame = decode_frame(raw)
        except (ParseError, struct.error):
            self.parse_errors += 1
            return None
        self.frames += 1
        if not frame.crc_ok:
            self.crc_errors += 1
        return frame
