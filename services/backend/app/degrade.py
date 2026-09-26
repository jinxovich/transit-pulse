"""Режим работы системы для баннеров дашборда: LIVE / WARMING_UP / DEGRADED / PAUSED / OFFLINE.

* нет сессии или она остановлена → OFFLINE;
* пауза replayer'а → PAUSED (это не обрыв потока);
* ``running`` и нет пакетов ``degraded_after`` секунд настенного времени → DEGRADED;
* прогрев (объявлен replayer'ом или у всех ТС мало истории) → WARMING_UP;
* иначе LIVE.
"""

from __future__ import annotations

from dataclasses import dataclass

from transit_core import schemas as S

from .state.clock import SimClock

REASON_OFFLINE = "Нет данных от источника: сессия воспроизведения не запущена"
REASON_PAUSED = "Воспроизведение на паузе"
REASON_WARMUP = "Прогрев: копится история телеметрии, инциденты пока не создаются"
REASON_DEGRADED = "Нет пакетов NDTP {age:.0f} с — прогноз по расписанию и последнему состоянию"


@dataclass(frozen=True)
class ModeInputs:
    """Всё, от чего зависит режим (настенное время передаётся явно)."""

    wall: float
    last_packet_wall: float | None
    session_wall: float  # когда началась текущая сессия
    all_warming: bool
    degraded_after_s: float


def packet_age(inp: ModeInputs) -> float | None:
    """Секунд с последнего пакета (``None`` — пакетов ещё не было)."""
    if inp.last_packet_wall is None:
        return None
    return max(inp.wall - inp.last_packet_wall, 0.0)


def compute_mode(clock: SimClock, inp: ModeInputs) -> tuple[S.StreamMode, str | None]:
    """Режим и пояснение для баннера."""
    if not clock.has_session or clock.state == "stopped":
        return "OFFLINE", REASON_OFFLINE
    if clock.state == "paused":
        return "PAUSED", REASON_PAUSED
    last = inp.last_packet_wall
    ref = max(last, inp.session_wall) if last is not None else inp.session_wall
    silence = inp.wall - ref
    if silence >= inp.degraded_after_s:
        return "DEGRADED", REASON_DEGRADED.format(age=silence)
    if clock.in_warmup(inp.wall) or inp.all_warming:
        return "WARMING_UP", REASON_WARMUP
    return "LIVE", None
