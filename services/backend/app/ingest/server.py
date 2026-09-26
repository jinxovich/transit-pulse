"""TCP-сервер NDTP (порт 9201): только парсинг кадров и постановка в очередь.

На каждое соединение — свой потоковый :class:`FrameDecoder`; на каждый запрос терминала
уходит ``NPH_RESULT``. Телематические кадры кладутся в ограниченную очередь вместе с
настенным временем приёма; в state их пишет :mod:`.worker`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from .codec import NPH_CONN_REQUEST, FrameDecoder, encode_result
from .stats import DropOldestQueue, IngestStats

log = logging.getLogger(__name__)
READ_CHUNK = 65536
RESULT_CRC_ERROR = 2


class IngestServer:
    """asyncio TCP-сервер приёма NDTP."""

    def __init__(
        self,
        stats: IngestStats,
        queue: DropOldestQueue,
        wall: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stats = stats
        self.queue = queue
        self.wall = wall
        self._server: asyncio.Server | None = None

    async def start(self, host: str, port: int) -> int:
        """Запускает сервер; возвращает фактический порт (для ``port=0`` в тестах)."""
        self._server = await asyncio.start_server(self.handle, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 1.0)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Обслуживает одно соединение терминала до его закрытия."""
        decoder = FrameDecoder()
        self.stats.connections += 1
        crc0 = parse0 = 0
        try:
            while data := await reader.read(READ_CHUNK):
                wall = self.wall()
                replies = self._on_frames(decoder.feed(data), wall)
                self.stats.crc_errors += decoder.crc_errors - crc0
                self.stats.parse_errors += decoder.parse_errors - parse0
                crc0, parse0 = decoder.crc_errors, decoder.parse_errors
                if replies:
                    writer.write(b"".join(replies))
                    await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:  # noqa: BLE001 — одно битое соединение не должно ронять ingest
            log.exception("Ошибка в NDTP-соединении")
        finally:
            self.stats.connections -= 1
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _on_frames(self, frames: list, wall: float) -> list[bytes]:
        replies = []
        for frame in frames:
            self.stats.on_frame(frame, wall)
            if not frame.crc_ok:  # битые данные в state не пускаем, терминал переотправит
                replies.append(encode_result(frame, RESULT_CRC_ERROR))
                continue
            if frame.nph_type == NPH_CONN_REQUEST:
                self.stats.on_handshake(frame.unit_id)
            elif frame.nav is not None:
                self.queue.put((wall, frame))
            replies.append(encode_result(frame))
        return replies
