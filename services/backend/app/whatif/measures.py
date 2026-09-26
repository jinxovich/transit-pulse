"""Упреждающие меры what-if как преобразование признаков модели.

Мера не требует отдельной модели: она меняет те признаки, через которые модель «видит»
отклонение от графика (``cur_dev`` — по остановкам, ``gps_dev`` — по нитке GPS,
``eta_dev`` — ожидаемое по ETA), а остальные признаки (скорости, стоянка, свойства
цели) остаются как есть. Дальше — тот же ML-клиент, что и в проходе прогнозов.

* ``hold_at_stop`` — придержать на остановке N мин: отклонение +N для всех остановок
  (поглощение на конечной модель учитывает сама по ``trip_break_between``);
* ``shorten_dwell`` — сократить отстой на конечной на N мин: −N к отклонению остановок
  после разрыва рейса, но не больше планового отстоя и не раньше графика;
* ``skip_layover`` — выпустить без отстоя: как ``shorten_dwell`` на весь плановый отстой;
* ``add_reserve`` — подменный выпуск резервного ТС: рейс после конечной идёт по графику,
  отклонение остановок после разрыва = 0.

Ответ модели проходит «монотонный» фильтр (:func:`guard`): придержать не может уменьшить
задержку, а меры на конечной — увеличить её или увести прогноз раньше графика (модель
немонотонна в пределах своей ошибки, диспетчеру такой шум не нужен).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from transit_core import schemas as S

DEV_KEYS = ("cur_dev", "gps_dev", "eta_dev")
"""Признаки отклонения от графика, которые сдвигает мера."""
DEFAULT_MIN: dict[str, float] = {"hold_at_stop": 2.0, "shorten_dwell": 3.0}
"""Величина меры по умолчанию, мин."""
TITLES: dict[str, str] = {
    "hold_at_stop": "Придержать на остановке {m:g} мин",
    "shorten_dwell": "Сократить отстой на конечной на {m:g} мин",
    "skip_layover": "Выпустить с конечной без отстоя",
    "add_reserve": "Резервный выпуск на рейс после конечной",
}


@dataclass(frozen=True)
class Measure:
    """Мера, приведённая к минутам."""

    action: S.WhatIfAction
    minutes: float

    @property
    def title(self) -> str:
        return TITLES[self.action].format(m=self.minutes)

    @property
    def needs_break(self) -> bool:
        """Мера действует только на остановки после конечной."""
        return self.action != "hold_at_stop"


def measure_of(req: S.WhatIfRequest) -> Measure:
    """Мера из запроса; без ``value`` — величина по умолчанию."""
    if req.action in DEFAULT_MIN:
        value = DEFAULT_MIN[req.action] if req.value is None else req.value
        return Measure(req.action, float(value))
    return Measure(req.action, 0.0)


def _num(v: float | None) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def is_after_break(features: Mapping[str, float]) -> bool:
    """Между «сейчас» и остановкой есть конечная (разрыв рейса)."""
    return _num(features.get("trip_break_between")) == 1.0


def layover_min(features: Mapping[str, float]) -> float:
    """Плановый отстой на конечной до остановки, мин (0, если не известен)."""
    return max(_num(features.get("max_gap_between")) or 0.0, 0.0)


def _absorb(v: float, cut_s: float) -> float:
    """Отклонение после сокращения отстоя: не больше ``cut_s`` и не раньше графика."""
    return max(v - cut_s, min(v, 0.0))


def _shift(features: Mapping[str, float], fn) -> dict[str, float]:
    """Новые признаки: ``fn`` применяется к известным признакам отклонения."""
    out = dict(features)
    for k in DEV_KEYS:
        v = _num(features.get(k))
        if v is not None:
            out[k] = fn(v)
    return out


def apply_measure(m: Measure, features: Mapping[str, float]) -> dict[str, float]:
    """Признаки остановки «после меры» (исходный словарь не меняется)."""
    if m.action == "hold_at_stop":
        return _shift(features, lambda v: v + m.minutes * 60)
    if not is_after_break(features):
        return dict(features)
    if m.action == "add_reserve":
        return _shift(features, lambda v: 0.0)
    layover = layover_min(features)
    cut = layover if m.action == "skip_layover" else min(m.minutes, layover)
    return _shift(features, lambda v: _absorb(v, cut * 60))


def applied_minutes(m: Measure, stops_features: list[Mapping[str, float]]) -> float:
    """Фактическая величина меры: для ``skip_layover`` — плановый отстой на конечной."""
    if m.action != "skip_layover":
        return m.minutes
    return max((layover_min(f) for f in stops_features if is_after_break(f)), default=0.0)


def note_for(m: Measure, has_break: bool, changed: bool, target_after_break: bool) -> str:
    """Пояснение для диспетчера: как мера учтена и почему может не влиять."""
    if m.needs_break and not has_break:
        return "В ближайший час у ТС нет конечной — мера на график не влияет."
    if not changed:
        return "ТС идёт по графику или раньше — мере нечего сократить, прогноз тот же."
    if m.needs_break and not target_after_break:
        return "Цель — до конечной: мера сказывается только на следующем рейсе (после конечной)."
    if m.action == "hold_at_stop":
        return (f"Отклонение от графика сдвинуто на +{m.minutes:g} мин для всех остановок; "
                "поглощение задержки на конечной модель учитывает сама.")  # fmt: skip
    if m.action == "add_reserve":
        return "Рейс после конечной выполняет резервное ТС по графику: отклонение = 0."
    return ("Для остановок после конечной отклонение уменьшено на сокращённый отстой "
            "(не больше планового и не раньше графика).")  # fmt: skip


def guard(m: Measure, before: tuple[float, float], after: tuple[float, float]) -> tuple:
    """(задержка, p_late) с мерой после монотонного фильтра; ``before`` — без меры."""
    (d0, p0), (d1, p1) = before, after
    if m.action == "hold_at_stop":
        return max(d1, d0), max(p1, p0)
    return min(max(d1, min(d0, 0.0)), d0), min(p1, p0)
