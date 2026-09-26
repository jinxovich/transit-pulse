"""Сим-часы и сессии воспроизведения (``docs/INTERFACES.md`` §2).

Сим-время идёт по формуле ``sim_now = last_wm + (wall − wall_at_wm) × speed``, пока
сессия в состоянии ``running``; на паузе часы стоят. Пакеты известных бортов лишь
подтягивают водяной знак вперёд; сообщение replayer'а о сессии выставляет его точно. Настенное время
(``wall``, секунды ``time.monotonic``) всегда передаётся явно — так часы тестируются
без ``sleep``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from transit_core import schemas as S

from ..config import DATASET_DAY
from .timefmt import fmt

ClockState = Literal["running", "paused", "stopped"]
NO_SESSION = "none"
AUTO_SPEED = 1.0
MIN_TOLERANCE_S = 30.0
TOLERANCE_WALL_S = 1.0
REWIND_IGNORE_WALL_S = 1.0


@dataclass(frozen=True)
class SessionUpdate:
    """Сообщение replayer'а о сессии (тело ``POST /internal/sim/session``)."""

    session_id: str
    sim_time: datetime
    speed: float
    state: ClockState
    warmup_until: datetime | None = None


class SimClock:
    """Сим-часы текущей сессии."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.speed = AUTO_SPEED
        self.state: ClockState = "stopped"
        self.warmup_until: datetime | None = None
        self.auto = False
        self._wm: datetime = DATASET_DAY
        self._wall_at_wm = 0.0
        self._auto_n = 0

    @property
    def has_session(self) -> bool:
        """Есть ли активная (не остановленная) сессия."""
        return self.session_id is not None and self.state != "stopped"

    def now(self, wall: float) -> datetime:
        """Текущее сим-время по формуле из INTERFACES §2.

        Во время прогрева часы не уходят дальше ``warmup_until``: на ×600 экстраполяция
        до следующего сообщения replayer'а иначе проскочила бы на минуты вперёд.
        """
        if self.state != "running":
            return self._wm
        t = self._wm + timedelta(seconds=max(wall - self._wall_at_wm, 0.0) * self.speed)
        if self.warmup_until is not None and self._wm < self.warmup_until < t:
            return self.warmup_until
        return t

    def _set_wm(self, sim: datetime, wall: float) -> None:
        self._wm, self._wall_at_wm = sim, wall

    def apply(self, upd: SessionUpdate, wall: float) -> bool:
        """Применяет сообщение о сессии; ``True`` — началась новая сессия."""
        is_new = upd.session_id != self.session_id
        # Часы replayer'а — эталон. Мелкий откат (сетевая задержка heartbeat'а) игнорируем,
        # чтобы sim_time в WS не шёл назад.
        cur = self.now(wall)
        small_rewind = timedelta(seconds=REWIND_IGNORE_WALL_S * self.speed)
        keep = not is_new and self.state == "running" and upd.state == "running"
        if keep and upd.sim_time <= cur <= upd.sim_time + small_rewind:
            self._set_wm(cur, wall)
        else:
            self._set_wm(upd.sim_time, wall)
        self.session_id, self.speed, self.state = upd.session_id, upd.speed, upd.state
        self.warmup_until = upd.warmup_until
        self.auto = False
        return is_new

    def start_auto(self, et: datetime, wall: float) -> str:
        """Сессия без replayer'а (сырой NDTP-поток): часы идут за пакетами, скорость ×1."""
        self._auto_n += 1
        sid = f"auto-{self._auto_n:04d}"
        self.apply(SessionUpdate(sid, et, AUTO_SPEED, "running"), wall)
        self.auto = True
        return sid

    def tolerance_s(self) -> float:
        """Насколько пакет может «обогнать» часы, не считаясь хвостом чужой сессии."""
        return max(MIN_TOLERANCE_S, TOLERANCE_WALL_S * self.speed)

    def accepts(self, et: datetime, wall: float) -> bool:
        """Можно ли принять точку с временем ``et`` в текущую сессию."""
        if self.auto:
            return True
        return (et - self.now(wall)).total_seconds() <= self.tolerance_s()

    def observe(self, et: datetime, wall: float) -> None:
        """В авто-сессии (без replayer'а) часы идут за пакетами, вперёд и никогда назад.

        При сессии replayer'а часы двигают только его сообщения: одиночный кадр-хвост
        прошлой сессии или битое время не должны уводить часы вперёд.
        """
        if self.auto and self.state == "running" and et > self.now(wall):
            self._set_wm(et, wall)

    def in_warmup(self, wall: float) -> bool:
        """Идёт ли прогрев, объявленный replayer'ом (``warmup_until``)."""
        return self.warmup_until is not None and self.now(wall) < self.warmup_until

    def contract(self, wall: float) -> S.SimClock:
        """Ответ ``GET /api/v1/sim/clock``."""
        return S.SimClock(
            sim_time=fmt(self.now(wall)),
            session_id=self.session_id or NO_SESSION,
            speed=self.speed,
            state=self.state,
        )
