"""Политика открытия инцидента — отдельно от раскраски риска на карте.

Красный цвет (``risk_of``: delay > 120 или p_late ≥ 0.5) показывает диспетчеру всё
рискованное, а инцидент открывается строже: по своим порогам и только если визит окна
удовлетворял условию ``min_streak`` проходов подряд — одиночные «мигающие» красные
прогнозы давали больше ложных тревог, чем попаданий. Значения по умолчанию подобраны
реплеем на честных прогнозах (``docs/perf/alert_policy.md``).
"""

from __future__ import annotations

from dataclasses import dataclass

from transit_core import schemas as S

ALERT_MODES = ("or", "and")


@dataclass(frozen=True)
class AlertPolicy:
    """Условие алерта: ``delay > delay_s`` ``mode`` ``p_late ≥ p_late``, серия ≥ min_streak."""

    delay_s: float = 150.0
    p_late: float = 0.6
    mode: str = "or"
    min_streak: int = 2

    def __post_init__(self) -> None:
        if self.mode not in ALERT_MODES:
            raise ValueError(f"ALERT_MODE должен быть одним из {ALERT_MODES}, а не {self.mode!r}")
        if self.min_streak < 1:
            raise ValueError(f"ALERT_MIN_STREAK должен быть ≥ 1, а не {self.min_streak}")

    def matches(self, delay_s: float, p_late: float) -> bool:
        """Прогноз ``(delay_s, p_late)`` удовлетворяет условию алерта (без серии)."""
        by_delay = delay_s > self.delay_s
        by_p = p_late >= self.p_late
        return by_delay and by_p if self.mode == "and" else by_delay or by_p

    def hit(self, p: S.Prediction) -> bool:
        """Прогноз визита удовлетворяет условию алерта (без учёта серии и горизонта)."""
        return self.matches(p.predicted_delay_s, p.p_late)
