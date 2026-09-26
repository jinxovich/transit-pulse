"""Соединение юнита переживает обрыв со стороны сервера (рестарт backend)."""

import asyncio
import contextlib

import pytest
from replayer.link import LinkStats, UnitLink

from transit_core.ndtp import NavCell

NAV = NavCell(
    timestamp=1_767_682_800, lon=37.6, lat=55.7, valid=True, speed_kmh=20, speed_max_kmh=25,
    course=90, track_m=0, altitude_m=150, nsat=0, pdop=0, bat_voltage=200, flags=0xE0,
)  # fmt: skip


async def _wait_for(predicate, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("условие не наступило")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_link_reconnects_after_server_closes_connection():
    accepted: list[asyncio.StreamWriter] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.append(writer)
        if len(accepted) == 1:
            await reader.read(64)  # handshake
            writer.close()  # сервер «перезапустился» и оборвал соединение
            return
        while await reader.read(4096):
            pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    stats = LinkStats()
    link = UnitLink(101, "127.0.0.1", port, stats, handshake_pause_s=0.01)
    task = asyncio.create_task(link.run())
    try:
        await _wait_for(lambda: len(accepted) >= 1 and link.connected)
        link.send(NAV)
        await _wait_for(lambda: len(accepted) >= 2 and link.connected)

        assert not task.done(), "задача соединения умерла вместо реконнекта"
        assert stats.reconnects >= 1
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for writer in accepted:
            writer.close()
        server.close()
        await server.wait_closed()
