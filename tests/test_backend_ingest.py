"""Ingest NDTP: TCP-сервер, порезанные кадры, статистика, очередь с вытеснением."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from backend_kit import FakeWall, handshake, has_raw, make_runtime, nav_frame

from services.backend.app.ingest.codec import FrameDecoder
from services.backend.app.ingest.server import IngestServer
from services.backend.app.ingest.stats import DropOldestQueue, IngestStats
from services.backend.app.ingest.worker import apply_frame
from transit_core import schemas as S

T0 = datetime(2026, 1, 6, 7, 0, 0)
UNIT, TR = 663271, 116445
needs_data = pytest.mark.skipif(not has_raw(), reason="нет data/raw")


def _stream(n: int) -> bytes:
    frames = [handshake(UNIT)]
    frames += [nav_frame(UNIT, T0 + timedelta(seconds=10 * i), 37.6 + i * 1e-4) for i in range(n)]
    return b"".join(frames)


async def _send_chunked(port: int, data: bytes, chunk: int) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    for i in range(0, len(data), chunk):
        writer.write(data[i : i + chunk])
        await writer.drain()
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)
    replies = await asyncio.wait_for(reader.read(65536), 1.0)
    writer.close()
    await writer.wait_closed()
    await asyncio.sleep(0.01)
    return replies


@pytest.mark.asyncio
async def test_tcp_server_accepts_split_frames_and_counts():
    stats = IngestStats()
    queue = DropOldestQueue(100, stats)
    server = IngestServer(stats, queue)
    port = await server.start("127.0.0.1", 0)
    try:
        replies = await _send_chunked(port, b"\x00\x01garbage" + _stream(5), chunk=7)
    finally:
        await server.stop()
    assert stats.packets_total == 6
    assert queue.qsize() == 5  # handshake в очередь не идёт
    assert stats.connections == 0 and stats.crc_errors == 0
    assert replies.startswith(b"\x7e\x7e")  # NPH_RESULT на запросы
    ingest = S.IngestStats.model_validate(stats.contract(wall=0).model_dump())
    assert ingest.last_packet.unit_id == UNIT and ingest.last_packet.fields["valid"] is True


@pytest.mark.asyncio
async def test_reconnect_counted_and_bad_crc_accepted():
    stats = IngestStats()
    queue = DropOldestQueue(100, stats)
    server = IngestServer(stats, queue)
    port = await server.start("127.0.0.1", 0)
    bad = bytearray(nav_frame(UNIT, T0))
    bad[-1] ^= 0xFF  # порча тела → CRC не сходится: в очередь не попадает
    try:
        await _send_chunked(port, handshake(UNIT), chunk=100)
        await _send_chunked(port, handshake(UNIT) + bytes(bad), chunk=100)
    finally:
        await server.stop()
    assert stats.reconnects == 1
    assert stats.crc_errors == 1 and queue.qsize() == 0


@pytest.mark.asyncio
async def test_queue_drops_oldest_when_full():
    stats = IngestStats()
    q = DropOldestQueue(3, stats)
    for i in range(5):
        q.put(i)
    assert stats.dropped == 2
    assert [q.get_nowait() for _ in range(3)] == [2, 3, 4]


def test_pps_sliding_window():
    stats = IngestStats()
    frame = FrameDecoder().feed(nav_frame(UNIT, T0))[0]
    for i in range(50):
        stats.on_frame(frame, 100.0 + i * 0.1)
    assert stats.pps(105.0) == pytest.approx(5.0)
    assert stats.pps(200.0) == 0.0


@needs_data
@pytest.mark.asyncio
async def test_end_to_end_tcp_to_state():
    wall = FakeWall()
    rt = make_runtime(wall)  # без replayer'а: авто-сессия, часы идут за пакетами
    server = IngestServer(rt.stats, rt.queue, wall)
    port = await server.start("127.0.0.1", 0)
    try:
        await _send_chunked(port, _stream(20), chunk=13)
    finally:
        await server.stop()
    while rt.queue.qsize():
        recv, frame = rt.queue.get_nowait()
        assert apply_frame(rt, recv, frame)
    rec = rt.store.vehicles[str(TR)]
    assert len(rec.points) == 20 and rec.kind == "no_schedule"
    assert rec.last_et == T0 + timedelta(seconds=190)
    assert rt.metrics.stats("ingest_to_state").p50_ms is not None
