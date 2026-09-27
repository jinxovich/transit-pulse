"""Прогон потока validate через настоящий путь backend в одном процессе.

Путь данных тот же, что в демо: строки ``validate/traffic.csv`` → ячейки NDTP
(:func:`replayer.source.load_track`, как у replayer'а) → TCP → :class:`IngestServer`
backend'а → очередь → :func:`apply_frame` (state) → :class:`PipelineRunner.run_pass`
(признаки, стоп-детектор) → ML-сервис (FastAPI-приложение ``services.ml.app.serve``
через ASGI-транспорт) → прогнозы и инциденты.

Отличие от демо одно: настенные часы инжектируются (:class:`FakeWall`), поэтому прогон
идёт с максимальной скоростью без ``sleep``, а проход прогнозов выполняется ровно на
каждой сим-минуте (как на скорости, при которой планировщик не пропускает минуты).
"""

from __future__ import annotations

import asyncio
import calendar
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "services" / "replayer") not in sys.path:
    sys.path.insert(0, str(ROOT / "services" / "replayer"))

from replayer.source import Track, load_track  # noqa: E402

from services.backend.app.config import Settings  # noqa: E402
from services.backend.app.ingest.codec import encode_handshake, encode_realtime  # noqa: E402
from services.backend.app.ingest.server import IngestServer  # noqa: E402
from services.backend.app.ingest.worker import apply_frame  # noqa: E402
from services.backend.app.pipeline.scheduler import PipelineRunner  # noqa: E402
from services.backend.app.runtime import Runtime  # noqa: E402
from services.backend.app.state.clock import SessionUpdate  # noqa: E402
from services.backend.app.state.static import load_static, load_typical_speeds  # noqa: E402
from services.ml.app import serve  # noqa: E402
from services.ml.app.inference import load_registry  # noqa: E402

WALL0 = 1000.0
DELIVERY_TIMEOUT_S = 10.0
SESSION_ID = "stream-eval"
ML_URL = "http://ml.inproc"


class FakeWall:
    """Настенные часы, которые двигает прогон (``time.monotonic`` в проде)."""

    def __init__(self, t: float = WALL0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@dataclass(frozen=True)
class RunConfig:
    """Параметры прогона: окно сим-времени, скорость сессии, фильтр бортов."""

    start: datetime
    end: datetime
    speed: float = 30.0
    tr_ids: frozenset[int] | None = None
    data_dir: Path = ROOT / "data" / "raw"
    models_dir: Path = ROOT / "models"
    typical_speeds: bool = True


@dataclass
class StreamLog:
    """Всё, что прогон записал: прогнозы, минуты ТС, проходы, инциденты и признаки
    каждого прогноза окна (нужны для честного реплея политики алертов)."""

    preds: list[dict] = field(default_factory=list)
    vehicle_minutes: list[dict] = field(default_factory=list)
    passes: list[dict] = field(default_factory=list)
    features: dict[tuple[int, datetime, int], dict] = field(default_factory=dict)
    incidents: list[dict] = field(default_factory=list)
    ingest: dict = field(default_factory=dict)
    wall_s: float = 0.0


class CapturingRunner(PipelineRunner):
    """Планировщик backend'а, который дополнительно пишет все прогнозы окна в журнал.

    Логика прохода не меняется: перехватывается только ``_update_record``, куда
    планировщик передаёт готовые прогнозы по всем визитам окна ``(T+10, T+15]``.
    """

    def __init__(self, rt: Runtime, log: StreamLog, keys: set[tuple[int, datetime]]) -> None:
        super().__init__(rt)
        self.log, self.keys = log, keys

    def _update_record(self, rec, task, preds, t) -> None:
        super()._update_record(rec, task, preds, t)
        self.log.vehicle_minutes.append({
            "tr_id": task.tr_id, "T": t, "stale": task.stale, "ready": task.ready,
            "n_visits": len(task.visits), "horizon_ok": task.horizon_ok, "cur_dev": task.cur_dev,
        })  # fmt: skip
        for vp in preds:
            p = vp.prediction
            self.log.preds.append({
                "tr_id": task.tr_id, "T": t, "visit_id": int(vp.visit.visit_id),
                "generated_at": p.generated_at, "planned_at": p.target_stop.planned_at,
                "lead_min": p.lead_min, "horizon_ok": p.horizon_ok, "delay_s": p.predicted_delay_s,
                "p_late": p.p_late, "q10": p.interval_s[0], "q90": p.interval_s[1],
                "risk": p.risk_level, "mode": p.model_mode, "cur_dev": task.cur_dev,
                "stale": task.stale, "first": vp is preds[0],
            })  # fmt: skip
            self.log.features[(task.tr_id, t, int(vp.visit.visit_id))] = dict(vp.visit.features)


class TcpFleet:
    """TCP-соединения бортов к NDTP-порту backend'а: handshake, затем realtime-кадры."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.writers: dict[int, asyncio.StreamWriter] = {}
        self.request_ids: dict[int, int] = {}
        self.readers: list[asyncio.Task] = []
        self.sent = 0

    async def _open(self, unit_id: int) -> asyncio.StreamWriter:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.readers.append(asyncio.create_task(_drain_replies(reader)))
        writer.write(encode_handshake(unit_id, 1))
        self.writers[unit_id], self.request_ids[unit_id] = writer, 1
        self.sent += 1
        return writer

    async def send(self, unit_id: int, nav) -> None:
        writer = self.writers.get(unit_id) or await self._open(unit_id)
        self.request_ids[unit_id] += 1
        writer.write(encode_realtime(unit_id, self.request_ids[unit_id], nav))
        self.sent += 1

    async def flush(self) -> None:
        for writer in self.writers.values():
            await writer.drain()

    async def close(self) -> None:
        for writer in self.writers.values():
            writer.close()
        for task in self.readers:
            task.cancel()


async def _drain_replies(reader: asyncio.StreamReader) -> None:
    """Вычитывает ``NPH_RESULT`` сервера, чтобы не забить TCP-буфер."""
    while await reader.read(65536):
        pass


def _epoch(t: datetime) -> int:
    return calendar.timegm(t.timetuple())


def _select_units(track: Track, rt: Runtime, tr_ids: frozenset[int] | None) -> set[int] | None:
    if tr_ids is None:
        return None
    return {u for u, tr in rt.static.unit_map.items() if tr in tr_ids}


def build_runtime(cfg: RunConfig, wall: FakeWall) -> Runtime:
    """Runtime backend'а с ML-сервисом в том же процессе (ASGI-транспорт httpx)."""
    reg = load_registry(cfg.models_dir)
    serve._warmup(reg)
    serve.STATE["reg"] = reg
    settings = Settings(data_dir=cfg.data_dir, models_dir=cfg.models_dir, ml_url=ML_URL,
                        ndtp_enabled=False, db_path=Path(":memory:"))  # fmt: skip
    transport = httpx.ASGITransport(app=serve.app)
    rt = Runtime(settings, static=load_static(cfg.data_dir), wall=wall, ml_transport=transport)
    if cfg.typical_speeds:
        rt.typical = load_typical_speeds(cfg.data_dir, rt.static)
    return rt


async def _deliver(rt: Runtime, expected: int) -> None:
    """Ждёт, пока сервер разберёт все отправленные кадры, и пишет их в state."""
    deadline = time.monotonic() + DELIVERY_TIMEOUT_S
    while rt.stats.packets_total < expected:
        if time.monotonic() > deadline:
            raise TimeoutError(f"ingest принял {rt.stats.packets_total} из {expected} кадров")
        await asyncio.sleep(0.001)
    while rt.queue.qsize():
        recv_wall, frame = rt.queue.get_nowait()
        apply_frame(rt, recv_wall, frame)


async def _pass(rt: Runtime, runner: PipelineRunner, t: datetime, log: StreamLog) -> None:
    rt.ml.last_latency_ms, n0 = None, len(log.preds)
    t0 = time.perf_counter()
    await runner.run_pass(t)
    wall_ms = (time.perf_counter() - t0) * 1000
    log.passes.append({"T": t, "wall_ms": wall_ms, "ml_ms": rt.ml.last_latency_ms,
                       "mode": rt.mode()[0], "n_preds": len(log.preds) - n0})  # fmt: skip
    rt.sweep(rt.mode()[0])


async def run_stream(cfg: RunConfig, keys: set[tuple[int, datetime]] | None = None) -> StreamLog:
    """Проигрывает окно ``[start, end]`` и возвращает журнал прогона."""
    log, wall = StreamLog(), FakeWall()
    rt = build_runtime(cfg, wall)
    await rt.ml.poll_health()
    track = load_track(cfg.data_dir / "validate" / "traffic.csv")
    units = _select_units(track, rt, cfg.tr_ids)
    server = IngestServer(rt.stats, rt.queue, wall)
    port = await server.start("127.0.0.1", 0)
    fleet = TcpFleet(port)
    runner = CapturingRunner(rt, log, keys or set())
    rt.apply_session(SessionUpdate(SESSION_ID, cfg.start, cfg.speed, "running"))
    i = track.index_at(_epoch(cfg.start))
    t, started = cfg.start, time.perf_counter()
    try:
        while t <= cfg.end:
            wall.t = WALL0 + (t - cfg.start).total_seconds() / cfg.speed
            end = track.index_at(_epoch(t) + 1)  # как replayer: все строки с ts <= sim
            for j in range(i, end):
                unit = int(track.unit_id[j])
                if units is None or unit in units:
                    await fleet.send(unit, track.nav(j))
            i = end
            await fleet.flush()
            await _deliver(rt, fleet.sent)
            await _pass(rt, runner, t, log)
            t += timedelta(minutes=1)
    finally:
        log.wall_s = time.perf_counter() - started
        await fleet.close()
        await server.stop()
        await rt.ml.close()
        rt.db.close()
    log.incidents = [inc.model_dump() for inc in rt.book.incidents.values()]
    log.ingest = {"frames_sent": fleet.sent, "packets_total": rt.stats.packets_total,
                  "crc_errors": rt.stats.crc_errors, "parse_errors": rt.stats.parse_errors,
                  "dropped": rt.stats.dropped}  # fmt: skip
    return log
