"""Статистика приёма NDTP и ограниченная очередь кадров."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque

from transit_core import schemas as S

from ..state.timefmt import utc_iso
from .codec import Frame, frame_fields

PPS_WINDOW_S = 10


class IngestStats:
    """Счётчики ingest; ``pps`` — по скользящему окну из посекундных корзин."""

    def __init__(self) -> None:
        self.packets_total = 0
        self.crc_errors = 0
        self.parse_errors = 0
        self.connections = 0
        self.reconnects = 0
        self.dropped = 0
        self.unknown_units: set[int] = set()
        self._last: tuple[Frame, float] | None = None
        self.last_packet_wall: float | None = None
        self._handshaken: set[int] = set()
        self._buckets: deque[list] = deque()

    def on_frame(self, frame: Frame, wall: float) -> None:
        """Учитывает принятый кадр (любой: handshake или телематика)."""
        self.packets_total += 1
        self.last_packet_wall = wall
        sec = math.floor(wall)
        if self._buckets and self._buckets[-1][0] == sec:
            self._buckets[-1][1] += 1
        else:
            self._buckets.append([sec, 1])
        while self._buckets and self._buckets[0][0] <= sec - PPS_WINDOW_S:
            self._buckets.popleft()
        self._last = (frame, time.time())

    def on_handshake(self, unit_id: int) -> None:
        """Повторный handshake того же борта — это переподключение."""
        if unit_id in self._handshaken:
            self.reconnects += 1
        self._handshaken.add(unit_id)

    def last_packet(self) -> S.LastPacket | None:
        """Последний кадр для витрины «hex + поля» (собирается лениво)."""
        if self._last is None:
            return None
        frame, received = self._last
        return S.LastPacket(
            received_at=utc_iso(received),
            unit_id=frame.unit_id,
            hex=frame.raw.hex(),
            fields=frame_fields(frame),
        )

    def pps(self, wall: float) -> float:
        """Пакетов в секунду за последние ``PPS_WINDOW_S`` секунд."""
        edge = math.floor(wall) - PPS_WINDOW_S
        total = sum(n for sec, n in self._buckets if sec > edge)
        return round(total / PPS_WINDOW_S, 2)

    def contract(self, wall: float) -> S.IngestStats:
        """Ответ ``GET /api/v1/ingest/stats``."""
        return S.IngestStats(
            packets_total=self.packets_total,
            pps=self.pps(wall),
            crc_errors=self.crc_errors,
            parse_errors=self.parse_errors,
            connections=self.connections,
            reconnects=self.reconnects,
            unknown_units=len(self.unknown_units),
            last_packet=self.last_packet(),
        )


class DropOldestQueue:
    """Ограниченная очередь: при переполнении выкидывает самый старый элемент."""

    def __init__(self, maxsize: int, stats: IngestStats) -> None:
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._stats = stats

    def put(self, item) -> None:
        """Кладёт без ожидания; старое вытесняется и считается в ``dropped``."""
        while True:
            try:
                self._q.put_nowait(item)
                return
            except asyncio.QueueFull:
                self._q.get_nowait()
                self._stats.dropped += 1

    async def get(self):
        return await self._q.get()

    def get_nowait(self):
        return self._q.get_nowait()

    def qsize(self) -> int:
        return self._q.qsize()

    def clear(self) -> int:
        """Выбрасывает всё (хвост старой сессии); возвращает число выброшенных."""
        n = 0
        while not self._q.empty():
            self._q.get_nowait()
            n += 1
        return n
