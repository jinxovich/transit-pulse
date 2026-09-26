"""Сырой NDTP-приёмник: пишет все входящие байты в файл и отвечает ``NPH_RESULT``.

Нужен, чтобы снять эталон с официального эмулятора (``tests/fixtures/emu.bin``) и
проверить replayer. Байты каждого соединения копятся целиком и дописываются в файл
при его закрытии (или остановке приёмника), чтобы потоки разных юнитов не
перемешались посреди кадра: файл — конкатенация сырых TCP-потоков.

Запуск::

    uv run python -m scripts.ndtp_capture --port 19201 --out emu.bin
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from transit_core.ndtp import FrameDecoder, encode_result, frame_fields

log = logging.getLogger("ndtp_capture")
READ_CHUNK = 65536


class Capture:
    """Состояние приёмника: файл для записи и счётчики."""

    def __init__(self, out: Path, verbose: bool) -> None:
        self.out = out.open("ab")
        self.verbose = verbose
        self.frames = 0
        self.crc_errors = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Обслуживает одно TCP-соединение до его закрытия."""
        peer = writer.get_extra_info("peername")
        log.info("connect %s", peer)
        decoder = FrameDecoder()
        stream = bytearray()
        try:
            while data := await reader.read(READ_CHUNK):
                stream += data
                for frame in decoder.feed(data):
                    self._on_frame(frame, writer)
                await writer.drain()
        except ConnectionError as exc:
            log.warning("обрыв %s: %s", peer, exc)
        finally:
            self.out.write(stream)
            self.out.flush()
            self.crc_errors += decoder.crc_errors
            log.info(
                "disconnect %s bytes=%d frames=%d crc_errors=%d parse_errors=%d",
                peer, len(stream), decoder.frames, decoder.crc_errors, decoder.parse_errors,
            )  # fmt: skip
            writer.close()

    def _on_frame(self, frame, writer: asyncio.StreamWriter) -> None:
        """Отвечает на кадр и пишет его краткое описание в лог."""
        self.frames += 1
        writer.write(encode_result(frame))
        if self.verbose:
            log.info("frame %s", frame_fields(frame))


async def serve(host: str, port: int, out: Path, verbose: bool) -> None:
    """Поднимает сервер и работает до отмены."""
    capture = Capture(out, verbose)
    server = await asyncio.start_server(capture.handle, host, port)
    log.info("слушаю %s:%d → %s", host, port, out)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT, stop.set)
    async with server:
        await stop.wait()
    server.close()
    for task in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
        task.cancel()
    await asyncio.sleep(0.1)
    log.info("итого кадров %d, crc_errors %d", capture.frames, capture.crc_errors)


def main() -> None:
    """CLI-точка входа."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19201)
    parser.add_argument("--out", type=Path, default=Path("emu.bin"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(serve(args.host, args.port, args.out, args.verbose))


if __name__ == "__main__":
    main()
