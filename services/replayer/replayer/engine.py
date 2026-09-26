"""Движок воспроизведения: сим-часы → точки истории → TCP-соединения юнитов."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from replayer.clock import ReplayClock
from replayer.config import Settings
from replayer.link import LinkStats, UnitLink
from replayer.session import SessionReporter
from replayer.source import Track, clone_unit_id

log = logging.getLogger("replayer.engine")
AUTOSTART_WAIT_S = 5.0
"""Сколько ждать соединений всех юнитов перед автостартом (чтобы не терять прогрев)."""
DAY_END = timedelta(hours=23, minutes=59)
"""Конец круга по умолчанию: 23:59 дня истории."""


def to_iso(ts: float) -> str:
    """Unix-секунды → наивное ``YYYY-MM-DDTHH:MM:SS`` (контракт ``NaiveTime``)."""
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%dT%H:%M:%S")


def from_iso(value: str) -> float:
    """Наивное ISO-время → unix-секунды (как UTC)."""
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


class ReplayEngine:
    """Воспроизводит ``Track`` через ``UnitLink`` и сообщает backend о сессии."""

    def __init__(
        self,
        settings: Settings,
        track: Track,
        reporter: SessionReporter,
        clock: ReplayClock | None = None,
    ) -> None:
        self.settings = settings
        self.track = track
        self.reporter = reporter
        self.clock = clock or ReplayClock(settings.speed, settings.warmup_speed)
        self.stats = LinkStats()
        self.session_id = ""
        self.loops = 0
        self._seq = 0
        self._cursor = 0
        self._links = self._make_links()
        self._tasks: list[asyncio.Task[Any]] = []
        day = datetime.fromtimestamp(int(track.ts[0]), UTC).replace(hour=0, minute=0, second=0)
        self.day_start = day.timestamp()
        self.clock.park(self.preset_start)

    def _make_links(self) -> dict[int, list[UnitLink]]:
        """Соединения на каждый реальный юнит и его клоны (``FLEET_MULTIPLIER``)."""
        s = self.settings
        return {
            unit: [
                UnitLink(
                    clone_unit_id(unit, k),
                    s.ndtp_host,
                    s.ndtp_port,
                    self.stats,
                    s.handshake_pause_s,
                )  # fmt: skip
                for k in range(s.fleet_multiplier)
            ]
            for unit in self.track.units
        }

    @property
    def links(self) -> list[UnitLink]:
        """Все соединения (реальные юниты и клоны)."""
        return [link for group in self._links.values() for link in group]

    @property
    def preset_start(self) -> float:
        """Начало основного воспроизведения (``REPLAY_START`` дня истории)."""
        start = self.settings.start
        return self.day_start + start.hour * 3600 + start.minute * 60 + start.second

    @property
    def loop_end(self) -> float:
        """Сим-время конца круга: ``REPLAY_LOOP_HOURS`` от старта или 23:59."""
        day_end = self.day_start + DAY_END.total_seconds()
        if self.settings.loop_hours is None:
            return day_end
        return min(day_end, self.preset_start + self.settings.loop_hours * 3600)

    async def start(self) -> None:
        """Поднимает соединения, воркеры и (при ``REPLAY_AUTOSTART``) воспроизведение."""
        self._tasks = [asyncio.create_task(link.run()) for link in self.links]
        self._tasks += [
            asyncio.create_task(self.reporter.run()),
            asyncio.create_task(self._tick_loop()),
            asyncio.create_task(self._heartbeat_loop()),
        ]
        if self.settings.autostart:
            self._tasks.append(asyncio.create_task(self._autostart()))

    async def _autostart(self) -> None:
        """Ждёт соединений (не дольше ``AUTOSTART_WAIT_S``) и стартует пресет."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + AUTOSTART_WAIT_S
        while not all(link.connected for link in self.links) and loop.time() < deadline:
            await asyncio.sleep(self.settings.tick_s)
        if not self.session_id:
            self.begin(self.preset_start)

    async def stop(self) -> None:
        """Гасит все фоновые задачи."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.reporter.aclose()

    def begin(self, target: float) -> None:
        """Новая сессия: прогрев с ``target − warmup`` до ``target``, затем основная скорость."""
        warmup_from = max(self.day_start, target - self.settings.warmup_min * 60)
        self._seq += 1
        wall = datetime.now(UTC)
        self.session_id = f"s-{wall:%Y%m%d-%H%M%S}-{self._seq:04d}"
        self.clock.start(warmup_from, target)
        self._cursor = self.track.index_at(warmup_from)
        log.info("сессия %s: %s → %s", self.session_id, to_iso(warmup_from), to_iso(target))
        self.report()

    def control(self, action: str, speed: float | None = None, seek_to: str | None = None) -> None:
        """Команда ``ReplayControl``; неверные аргументы — ``ValueError``."""
        if action == "start":
            self.begin(self.preset_start)
        elif action == "pause":
            self.clock.pause()
        elif action == "resume":
            self.clock.resume()
        elif action == "speed":
            if speed is None or speed <= 0:
                raise ValueError("speed: нужна положительная скорость")
            self.clock.set_speed(speed)
        elif action == "seek":
            self.begin(self._seek_target(seek_to))
        else:
            raise ValueError(f"неизвестное действие {action!r}")
        self.report()

    def _seek_target(self, seek_to: str | None) -> float:
        """Проверяет, что ``seek_to`` попадает в день истории."""
        if seek_to is None:
            raise ValueError("seek: нужен seek_to")
        target = from_iso(seek_to)
        if not self.day_start <= target < self.loop_end:
            raise ValueError(f"seek_to вне [{to_iso(self.day_start)}, {to_iso(self.loop_end)})")
        return target

    def tick(self) -> int:
        """Отправляет все точки с ``ts <= sim``; возвращает число поставленных в очередь."""
        if self.clock.state != "running":
            return 0
        if self.clock.advance():
            self.report()
        now = min(self.clock.now(), self.loop_end)
        sent = self._emit_until(now)
        if now >= self.loop_end:
            self._finish_loop()
        return sent

    def _emit_until(self, sim: float) -> int:
        """Раздаёт строки истории до ``sim`` включительно по соединениям юнитов."""
        track, sent = self.track, 0
        end = track.index_at(math.floor(sim) + 1)
        for i in range(self._cursor, end):
            for clone, link in enumerate(self._links[int(track.unit_id[i])]):
                sent += link.send(track.nav(i, clone))
        self._cursor = max(self._cursor, end)
        return sent

    def _finish_loop(self) -> None:
        """Конец круга: новая сессия при ``REPLAY_LOOP``, иначе остановка."""
        if self.settings.loop:
            self.loops += 1
            self.begin(self.preset_start)
        else:
            self.clock.stop()
            self.report()

    async def _tick_loop(self) -> None:
        """Тикает движок с шагом ``tick_s`` настенного времени."""
        while True:
            await asyncio.sleep(self.settings.tick_s)
            try:
                self.tick()
            except Exception:  # движок не должен умирать из-за одной строки
                log.exception("ошибка тика")

    async def _heartbeat_loop(self) -> None:
        """Раз в ``heartbeat_s`` повторяет сессию: backend после рестарта её подхватит."""
        while True:
            await asyncio.sleep(self.settings.heartbeat_s)
            if self.session_id:
                self.report()

    def session_payload(self) -> dict[str, Any]:
        """Тело ``POST /internal/sim/session`` (INTERFACES §2)."""
        warm = self.clock.warmup_until
        return {
            "session_id": self.session_id,
            "sim_time": to_iso(self.clock.now()),
            "speed": float(self.clock.effective_speed),
            "state": self.clock.state,
            "warmup_until": to_iso(warm) if warm is not None else None,
        }

    def report(self) -> None:
        """Ставит текущее состояние сессии на отправку в backend."""
        if self.session_id:
            self.reporter.push(self.session_payload())

    def status(self) -> dict[str, Any]:
        """Ответ ``GET /status``."""
        links = self.links
        return {
            **self.session_payload(),
            "units": len(links),
            "units_connected": sum(link.connected for link in links),
            "packets_sent": self.stats.packets_sent,
            "dropped": self.stats.dropped,
            "reconnects": self.stats.reconnects,
            "loops": self.loops,
            "fleet_multiplier": self.settings.fleet_multiplier,
            "session_posts_ok": self.reporter.sent,
            "session_posts_failed": self.reporter.errors,
        }
