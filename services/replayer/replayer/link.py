"""TCP-соединение одного юнита с NDTP-сервером: handshake → realtime, реконнект."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from dataclasses import dataclass

from transit_core.ndtp import NavCell, encode_handshake, encode_realtime

log = logging.getLogger("replayer.link")

QUEUE_MAX = 1000
BACKOFF_MIN_S = 0.5
BACKOFF_MAX_S = 10.0
READ_CHUNK = 4096
REQUEST_ID_MAX = 0xFFFF_FFFF


@dataclass
class LinkStats:
    """Общие счётчики всех соединений."""

    packets_sent: int = 0
    dropped: int = 0
    connects: int = 0
    reconnects: int = 0


class UnitLink:
    """Соединение терминала ``unit_id``: как эмулятор, но пакеты берёт из очереди.

    Пока соединения нет, пакеты отбрасываются (``stats.dropped``): при воспроизведении
    с ускорением устаревшие точки backend всё равно бы не принял как «текущие».
    """

    def __init__(
        self, unit_id: int, host: str, port: int, stats: LinkStats, handshake_pause_s: float = 0.2
    ) -> None:
        self.unit_id = unit_id
        self.host = host
        self.port = port
        self.stats = stats
        self.handshake_pause_s = handshake_pause_s
        self.connected = False
        self._queue: asyncio.Queue[NavCell] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._request_id = 0

    def send(self, nav: NavCell) -> bool:
        """Ставит точку в очередь отправки; False — отброшена."""
        if not self.connected:
            self.stats.dropped += 1
            return False
        try:
            self._queue.put_nowait(nav)
        except asyncio.QueueFull:
            self.stats.dropped += 1
            return False
        return True

    def _next_request_id(self) -> int:
        """Счётчик NPH ``requestId``: 1, 2, 3…, с переполнением как у эмулятора."""
        self._request_id = self._request_id % REQUEST_ID_MAX + 1
        return self._request_id

    async def run(self) -> None:
        """Держит соединение вечно: при ошибке ждёт с экспоненциальным backoff."""
        backoff = BACKOFF_MIN_S
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError as exc:
                log.debug("unit %d: нет соединения (%s)", self.unit_id, exc)
            else:
                backoff = BACKOFF_MIN_S
                await self._serve(reader, writer)
                self.stats.reconnects += 1
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Одна TCP-сессия: handshake, пауза, затем пакеты из очереди до обрыва."""
        self.stats.connects += 1
        self._request_id = 0
        eof = asyncio.create_task(_read_until_eof(reader))
        try:
            writer.write(encode_handshake(self.unit_id, self._next_request_id()))
            await writer.drain()
            await asyncio.sleep(self.handshake_pause_s)
            self.connected = True
            await self._pump(writer, eof)
        except (OSError, ConnectionError) as exc:
            log.info("unit %d: обрыв (%s)", self.unit_id, exc)
        finally:
            self.connected = False
            eof.cancel()
            self._drain_queue()
            writer.close()
            with contextlib.suppress(OSError, ConnectionError):
                await writer.wait_closed()

    async def _pump(self, writer: asyncio.StreamWriter, eof: asyncio.Task[None]) -> None:
        """Пишет realtime-кадры из очереди, пока сервер не закрыл соединение."""
        while True:
            get = asyncio.create_task(self._queue.get())
            try:
                await asyncio.wait({get, eof}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not get.done():
                    get.cancel()
            if get.cancelled():
                raise ConnectionError("сервер закрыл соединение")
            frame = encode_realtime(self.unit_id, self._next_request_id(), get.result())
            writer.write(frame)
            await writer.drain()
            self.stats.packets_sent += 1

    def _drain_queue(self) -> None:
        """Отбрасывает неотправленное при обрыве."""
        while not self._queue.empty():
            self._queue.get_nowait()
            self.stats.dropped += 1


async def _read_until_eof(reader: asyncio.StreamReader) -> None:
    """Вычитывает ответы сервера (``NPH_RESULT``), чтобы не забить TCP-буфер."""
    with contextlib.suppress(OSError, ConnectionError):
        while await reader.read(READ_CHUNK):
            pass
