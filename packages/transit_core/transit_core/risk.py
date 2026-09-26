"""Уровень риска задержки по порогам контракта (одинаково в бэкенде и моках)."""

from __future__ import annotations

from transit_core import schemas as S

RISK_ORDER: dict[str, int] = {"none": 0, "green": 1, "early": 2, "yellow": 3, "red": 4}
"""Порядок «серьёзности» уровней: для агрегатов (максимум по перегону и т.п.)."""


def risk_of(delay: float, p_late: float, th: S.Thresholds | None = None) -> S.RiskLevel:
    """Уровень риска по порогам контракта.

    red — ``delay > 120`` или ``p_late ≥ 0.5``; early — ``delay < −60``;
    yellow — ``delay ≥ 60`` или ``p_late ≥ 0.3``; иначе green.
    """
    th = th or S.Thresholds()
    if delay > th.red_delay_s or p_late >= th.red_p_late:
        return "red"
    if delay < th.early_delay_s:
        return "early"
    if delay >= th.yellow_delay_s or p_late >= th.yellow_p_late:
        return "yellow"
    return "green"
