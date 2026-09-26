"""Сим-часы replayer: ``sim = anchor + (wall − wall_anchor) × speed`` с прогревом."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

State = Literal["running", "paused", "stopped"]


@dataclass
class SimClock:
    """Часы симуляции в unix-секундах (наивное время истории как UTC).

    Пока ``sim < warmup_until`` часы идут со скоростью ``warmup_speed``, затем
    автоматически переключаются на ``speed``. ``wall`` — источник монотонного
    настенного времени (подменяется в тестах).
    """

    speed: float
    warmup_speed: float
    wall: Callable[[], float] = time.monotonic
    state: State = "stopped"
    warmup_until: float | None = None
    _anchor: float = 0.0
    _wall_anchor: float = field(default=0.0, repr=False)

    @property
    def effective_speed(self) -> float:
        """Текущая скорость: прогрев или основная."""
        return self.warmup_speed if self.warmup_until is not None else self.speed

    def start(self, sim_from: float, warmup_until: float | None) -> None:
        """Запускает часы с ``sim_from``; прогрев до ``warmup_until`` (если задан)."""
        self._anchor = sim_from
        self._wall_anchor = self.wall()
        self.warmup_until = warmup_until if warmup_until and warmup_until > sim_from else None
        self.state = "running"

    def park(self, sim: float) -> None:
        """Ставит остановленные часы на ``sim`` (до первого старта)."""
        self._anchor = sim
        self.warmup_until = None
        self.state = "stopped"

    def now(self) -> float:
        """Текущее сим-время (без побочных эффектов)."""
        if self.state != "running":
            return self._anchor
        elapsed = self.wall() - self._wall_anchor
        sim = self._anchor + elapsed * self.effective_speed
        if self.warmup_until is None or sim < self.warmup_until:
            return sim
        wall_at_switch = (self.warmup_until - self._anchor) / self.warmup_speed
        return self.warmup_until + (elapsed - wall_at_switch) * self.speed

    def advance(self) -> bool:
        """Переключает прогрев на основную скорость, если пора. True — переключились."""
        if self.state != "running" or self.warmup_until is None:
            return False
        sim = self.now()
        if sim < self.warmup_until:
            return False
        self._rebase(sim)
        self.warmup_until = None
        return True

    def pause(self) -> None:
        """Замораживает часы на текущем сим-времени."""
        if self.state == "running":
            self._anchor = self.now()
            self.state = "paused"

    def resume(self) -> None:
        """Продолжает ход с места паузы."""
        if self.state == "paused":
            self._wall_anchor = self.wall()
            self.state = "running"

    def stop(self) -> None:
        """Останавливает часы (конец истории без loop)."""
        self._anchor = self.now()
        self.state = "stopped"

    def set_speed(self, speed: float) -> None:
        """Явная смена скорости: применяется сразу и отменяет прогрев."""
        self._rebase(self.now())
        self.warmup_until = None
        self.speed = speed

    def _rebase(self, sim: float) -> None:
        """Переносит якорь в текущий момент, сохраняя сим-время."""
        self._anchor = sim
        self._wall_anchor = self.wall()
