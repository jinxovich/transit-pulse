"""Движок replayer против поддельного NDTP-сервера и поддельного backend HTTP."""

import asyncio
import json
from collections import defaultdict
from datetime import time
from pathlib import Path

import httpx
import pytest
from replayer.config import Settings
from replayer.engine import ReplayEngine, from_iso, to_iso
from replayer.session import SessionReporter
from replayer.source import CLONE_UNIT_STEP, load_track

from transit_core.ndtp import Frame, FrameDecoder, encode_result

HEADER = "unit_id,event_time,location_valid,lon,lat,alt,speed,heading,packet_id\n"
ROWS = [
    # вне порядка по времени, с дробными секундами и невалидными точками
    "101,2026-01-06 06:59:30.700000,True,37.60,55.70,150,20,90,1",
    "101,2026-01-06 06:59:10.200000,True,37.59,55.69,150,18,90,2",
    "202,2026-01-06 06:59:20.000000,False,,,,,,3",
    "202,2026-01-06 06:59:50.900000,True,37.70,55.80,160,0,0,4",
    "101,2026-01-06 07:00:20.000000,True,37.61,55.71,151,25,95,5",
    "202,2026-01-06 07:00:40.000000,False,,,,,,6",
    "101,2026-01-06 07:01:30.000000,True,37.62,55.72,152,30,100,7",
    "202,2026-01-06 06:00:00.000000,True,37.50,55.50,140,5,10,8",  # до прогрева
]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "validate").mkdir()
    (tmp_path / "validate" / "traffic.csv").write_text(HEADER + "\n".join(ROWS) + "\n")
    return tmp_path


class FakeNdtpServer:
    """Принимает соединения, декодирует кадры кодеком и отвечает NPH_RESULT."""

    def __init__(self) -> None:
        self.frames: dict[int, list[Frame]] = defaultdict(list)
        self.crc_errors = 0
        self.parse_errors = 0

    async def handle(self, reader, writer) -> None:
        dec = FrameDecoder()
        while data := await reader.read(4096):
            for frame in dec.feed(data):
                self.frames[frame.unit_id].append(frame)
                writer.write(encode_result(frame))
        self.crc_errors += dec.crc_errors
        self.parse_errors += dec.parse_errors
        writer.close()


def _settings(data_dir: Path, port: int, **kw) -> Settings:
    base = dict(
        data_dir=data_dir,
        ndtp_host="127.0.0.1",
        ndtp_port=port,
        backend_http="http://backend.test",
        start=time(7, 0),
        speed=600.0,
        loop=False,
        loop_hours=0.05,
        warmup_min=1.0,
        warmup_speed=600.0,
        autostart=False,
        handshake_pause_s=0.01,
        heartbeat_s=0.05,
        tick_s=0.01,
    )
    return Settings(**(base | kw))


async def _wait(pred, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        assert loop.time() < deadline, "не дождались условия"
        await asyncio.sleep(0.01)


async def _run(data_dir: Path, **kw):
    server = FakeNdtpServer()
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    posts: list[dict] = []

    def backend(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/sim/session"
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    settings = _settings(data_dir, port, **kw)
    client = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    reporter = SessionReporter(settings.backend_http, client)
    engine = ReplayEngine(settings, load_track(settings.traffic_csv), reporter)
    await engine.start()
    return server, srv, engine, posts


@pytest.mark.asyncio
async def test_engine_replays_history_over_ndtp_and_reports_session(data_dir):
    server, srv, engine, posts = await _run(data_dir)
    try:
        await _wait(lambda: engine.status()["units_connected"] == 2)
        engine.control("start")
        await _wait(lambda: engine.clock.state == "stopped" and engine.stats.packets_sent == 7)
        await _wait(lambda: posts and posts[-1]["state"] == "stopped")
    finally:
        await engine.stop()
        srv.close()

    assert set(server.frames) == {101, 202}
    assert server.crc_errors == 0 and server.parse_errors == 0
    for frames in server.frames.values():
        assert frames[0].handshake is not None
        stamps = [f.nav.timestamp for f in frames[1:]]
        assert stamps == sorted(stamps)
        assert [f.request_id for f in frames] == list(range(1, len(frames) + 1))

    navs_202 = [f.nav for f in server.frames[202][1:]]
    assert [to_iso(n.timestamp) for n in navs_202] == [
        "2026-01-06T06:59:20",  # 06:00 — раньше прогрева, не шлём
        "2026-01-06T06:59:50",  # дробные секунды округлены вниз
        "2026-01-06T07:00:40",
    ]
    assert (navs_202[0].valid, navs_202[0].lon, navs_202[0].lat) == (False, 0.0, 0.0)
    assert navs_202[1].valid and navs_202[1].lat == pytest.approx(55.8)

    first = posts[0]
    assert first["session_id"].startswith("s-") and first["state"] == "running"
    assert first["warmup_until"] == "2026-01-06T07:00:00"
    assert first["speed"] == 600.0
    assert {p["session_id"] for p in posts} == {first["session_id"]}


@pytest.mark.asyncio
async def test_seek_and_loop_start_new_sessions(data_dir):
    _, srv, engine, posts = await _run(data_dir, loop=True, speed=6000.0, loop_hours=0.1)
    try:
        engine.control("start")
        s1 = engine.session_id
        engine.control("seek", seek_to="2026-01-06T07:01:00")
        s2 = engine.session_id
        assert s2 != s1
        assert engine.session_payload()["warmup_until"] == "2026-01-06T07:01:00"
        await _wait(lambda: engine.loops >= 1)
        assert engine.session_id not in {s1, s2}
        with pytest.raises(ValueError):
            engine.control("seek", seek_to="2026-01-07T07:00:00")
        with pytest.raises(ValueError):
            engine.control("speed", speed=0)
        engine.control("pause")
        assert engine.status()["state"] == "paused"
    finally:
        await engine.stop()
        srv.close()
    assert len({p["session_id"] for p in posts}) >= 2


@pytest.mark.asyncio
async def test_backend_down_does_not_break_engine(data_dir):
    # NDTP-порт закрыт, HTTP падает — движок живёт, пакеты считаются потерянными.
    settings = _settings(data_dir, 1, backend_http="http://127.0.0.1:1")
    engine = ReplayEngine(
        settings, load_track(settings.traffic_csv), SessionReporter(settings.backend_http)
    )
    await engine.start()
    try:
        engine.control("start")
        await _wait(lambda: engine.clock.state == "stopped")
        await _wait(lambda: engine.reporter.errors >= 1)
        assert engine.stats.packets_sent == 0 and engine.stats.dropped == 7
    finally:
        await engine.stop()


def test_fleet_multiplier_clones_units(data_dir):
    settings = _settings(data_dir, 1, fleet_multiplier=3)
    engine = ReplayEngine(settings, load_track(settings.traffic_csv), SessionReporter("http://x"))
    ids = sorted(link.unit_id for link in engine.links)
    assert ids == sorted(u + CLONE_UNIT_STEP * k for u in (101, 202) for k in range(3))
    clone = engine.track.nav(1, clone=2)
    assert clone.lon == pytest.approx(engine.track.nav(1).lon + 0.004)


def test_iso_roundtrip():
    assert to_iso(from_iso("2026-01-06T07:00:00")) == "2026-01-06T07:00:00"


@pytest.mark.asyncio
async def test_autostart_waits_for_connections_and_drops_nothing(data_dir):
    server, srv, engine, _ = await _run(data_dir, autostart=True)
    try:
        await _wait(lambda: engine.clock.state == "stopped" and engine.stats.packets_sent == 7)
    finally:
        await engine.stop()
        srv.close()
    assert engine.stats.dropped == 0
    assert sum(len(f) for f in server.frames.values()) == 2 + 7


def test_backend_restart_restarts_session_with_warmup(data_dir):
    settings = _settings(data_dir, 1, speed=1.0)
    engine = ReplayEngine(settings, load_track(settings.traffic_csv), SessionReporter("http://x"))
    engine.begin(from_iso("2026-01-06T07:01:00"))
    engine.clock.start(from_iso("2026-01-06T07:01:00"), None)  # прогрев позади
    first = engine.session_id

    def links(up: bool) -> None:
        for link in engine.links:
            link.connected = up
        engine.watch_backend()

    links(True)
    links(False)
    assert engine.session_id == first  # пока backend лежит, сессию не трогаем
    links(True)

    assert engine.session_id != first
    assert engine.session_payload()["warmup_until"] == "2026-01-06T07:01:00"
    assert engine.clock.speed == settings.speed


def test_initial_connect_does_not_restart_session(data_dir):
    settings = _settings(data_dir, 1)
    engine = ReplayEngine(settings, load_track(settings.traffic_csv), SessionReporter("http://x"))
    engine.begin(from_iso("2026-01-06T07:01:00"))
    first = engine.session_id

    for link in engine.links:
        link.connected = True
    engine.watch_backend()

    assert engine.session_id == first
