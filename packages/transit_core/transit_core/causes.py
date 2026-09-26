"""Причина прогнозируемой задержки: правила (PLAN §5.7) + вклады признаков от ML.

Правила проверяются по порядку «самое конкретное → самое общее»; если ни одно не
сработало, причина берётся по признаку с наибольшим вкладом в прогноз. Тексты и
рекомендации — из :mod:`transit_core.catalog`.

Имена признаков ML-модели заранее не зафиксированы, поэтому для каждого смыслового
признака перечислены синонимы (``cur_dev`` / ``cur_dev_online`` / ...).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from transit_core import schemas as S
from transit_core.catalog import evidence, format_delay, make_cause

EARLY_S = -60.0
ACCUMULATED_S = 60.0
CONGESTION_RATIO = 0.5
CONGESTION_SPEED_KMH = 8.0
LONG_DWELL_S = 90.0
STOP_SHARE = 0.6
MAX_EVIDENCE = 3

ALIASES: dict[str, tuple[str, ...]] = {
    "cur_dev": ("cur_dev", "cur_dev_online", "cur_dev_s"),
    "dev_trend": ("gps_dev_trend", "dev_trend", "dev_trend15", "trend"),
    "speed_ratio": ("speed_ratio", "spd_ratio", "eta_speed_ratio"),
    "seg_speed": ("seg_speed",),
    "spd5": ("spd5", "speed_5m", "spd_5", "speed5"),
    "stop5": ("stop5", "stop_share5", "stop_share_5"),
    "dwell": ("dwell", "dwell_s", "dwell_cur"),
    "layover": ("max_gap_between", "layover", "layover_min", "trip_gap_min", "break_min"),
    "trip_break": ("trip_break_between",),
}
UNITS: dict[str, str] = {
    "dist_tgt": "{:.0f} м", "remain_m": "{:.0f} м", "gps_dist": "{:.0f} м",
    "lead": "{:.1f} мин", "seg_speed": "{:.1f} км/ч", "speed_ratio": "{:.2f}",
    "spd1": "{:.1f} км/ч", "spd15": "{:.1f} км/ч", "spd_last": "{:.0f} км/ч",
    "n_between": "{:.0f}", "invalid15": "{:.0%}", "trip_progress": "{:.0%}",
    "tgt_gap": "{:.0f} мин", "plan_run": "{:.0f} мин", "since_last_plan": "{:.0f} мин",
    "stale_valid": "{:.0f} с", "hour_sin": "{:.2f}", "hour_cos": "{:.2f}",
}  # fmt: skip
FLAGS = ("tgt_manual", "tgt_newtrip", "trip_break_between")
"""Признаки-флаги 0/1: в карточке — «да»/«нет»."""
ALWAYS_SHOWN = ("seg_speed", "speed_ratio", "dwell")
"""Производные признаки бэкенда, которые карточка показывает всегда (если посчитаны)."""
CONTRIB_CAUSE: dict[str, S.CauseCode] = {
    "cur_dev": "ACCUMULATED_DELAY",
    "dev_trend": "ACCUMULATED_DELAY",
    "speed_ratio": "CONGESTION",
    "spd5": "CONGESTION",
    "stop5": "LONG_DWELL",
    "dwell": "LONG_DWELL",
    "layover": "SHORT_LAYOVER",
    "gps_dev": "ACCUMULATED_DELAY",
    "eta_dev": "CONGESTION",
    "spd1": "CONGESTION",
    "spd15": "CONGESTION",
}


def _canon(feature: str) -> str | None:
    """Смысловое имя признака по синониму."""
    return next((k for k, names in ALIASES.items() if feature in names), None)


def feature(features: Mapping[str, float | None], name: str) -> float | None:
    """Значение смыслового признака ``name`` (по любому синониму); NaN → ``None``."""
    for alias in ALIASES.get(name, (name,)):
        v = features.get(alias)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return float(v)
    return None


def format_value(name: str, value: float | None) -> str:
    """Человекочитаемое значение признака для карточки инцидента."""
    if value is None:
        return "нет данных"
    canon = _canon(name) or name
    if name in FLAGS:
        return "да" if value >= 0.5 else "нет"
    if name in UNITS:
        return UNITS[name].format(value)
    if canon == "cur_dev" or name in ("gps_dev", "eta_dev", "stale"):
        return format_delay(value)
    if canon == "dwell":
        return f"{value:.0f} с"
    if canon == "dev_trend":
        return f"{value:+.0f} с / 5 мин"
    if canon == "spd5":
        return f"{value:.1f} км/ч"
    if canon == "stop5":
        return f"{value:.0%}"
    if canon == "layover":
        return f"{value:.0f} мин"
    return f"{value:.2f}"


def rule_cause(
    f: Mapping[str, float | None], predicted_delay_s: float, stale: bool
) -> S.CauseCode | None:
    """Код причины по правилам или ``None``, если ни одно не сработало."""
    cur, trend = feature(f, "cur_dev"), feature(f, "dev_trend")
    ratio, spd5 = feature(f, "speed_ratio"), feature(f, "spd5")
    dwell, stop5, layover = feature(f, "dwell"), feature(f, "stop5"), feature(f, "layover")
    if feature(f, "trip_break") == 0.0:
        layover = None  # до цели нет конечной — запаса на отстой тоже нет
    if stale:
        return "GPS_LOSS"
    if predicted_delay_s < EARLY_S:
        return "EARLY_RUNNING"
    if layover is not None and cur is not None and cur > 0 and cur / 60 > layover:
        return "SHORT_LAYOVER"
    slow = (ratio is not None and ratio < CONGESTION_RATIO) or (
        spd5 is not None and spd5 < CONGESTION_SPEED_KMH and (stop5 or 0) < STOP_SHARE
    )
    if slow and (trend is None or trend >= 0):
        return "CONGESTION"
    if (dwell is not None and dwell > LONG_DWELL_S) or (stop5 is not None and stop5 >= STOP_SHARE):
        return "LONG_DWELL"
    if cur is not None and cur > ACCUMULATED_S and (trend is None or trend >= 0):
        return "ACCUMULATED_DELAY"
    return None


def _top(contributions: Sequence[Mapping]) -> list[Mapping]:
    return sorted(contributions, key=lambda c: -abs(float(c.get("contribution_s") or 0)))


def _raw_value(f: Mapping[str, float | None], name: str) -> float | None:
    v = f.get(name)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def _evidence(f: Mapping[str, float | None], contributions: Sequence[Mapping]) -> list[S.Evidence]:
    """Топ вкладов ML (без них — ключевые признаки правил) + скорость на перегоне и простой."""
    out = []
    if contributions:
        for c in _top(contributions)[:MAX_EVIDENCE]:
            name = str(c["feature"])
            value = format_value(name, _raw_value(f, name))
            out.append(evidence(name, value, round(float(c.get("contribution_s") or 0), 1)))
    else:
        keys = ("cur_dev", "dev_trend", "spd5", "stop5", "layover")
        present = [k for k in keys if feature(f, k) is not None][:MAX_EVIDENCE]
        out = [evidence(k, format_value(k, feature(f, k)), None) for k in present]
    shown = {e.feature for e in out}
    for k in ALWAYS_SHOWN:
        v = _raw_value(f, k)
        if v is not None and k not in shown:
            out.append(evidence(k, format_value(k, v), None))
    return out


def infer_cause(
    features: Mapping[str, float | None],
    contributions: Sequence[Mapping] = (),
    predicted_delay_s: float = 0.0,
    stale: bool = False,
) -> S.Cause:
    """Причина прогноза с обоснованием (evidence) для карточки инцидента.

    ``contributions`` — вклады признаков от ML (``[{"feature", "contribution_s"}]``).
    """
    code = rule_cause(features, predicted_delay_s, stale) or _contrib_cause(contributions)
    return make_cause(code or "UNKNOWN", _evidence(features, contributions))


def _contrib_cause(contributions: Sequence[Mapping]) -> S.CauseCode | None:
    """Причина по признаку с наибольшим положительным вкладом."""
    for c in _top(contributions):
        name = str(c["feature"])
        code = CONTRIB_CAUSE.get(_canon(name) or name)
        if code is not None and float(c.get("contribution_s") or 0) > 0:
            return code
    return None
