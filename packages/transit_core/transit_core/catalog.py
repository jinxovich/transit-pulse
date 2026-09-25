"""Справочник причин задержек и рекомендаций диспетчеру.

Один источник текстов для бэкенда (карточка инцидента) и для моков дашборда.
"""

from __future__ import annotations

from transit_core.schemas import Cause, CauseCode, Evidence, Recommendation

CAUSES: dict[CauseCode, tuple[str, str]] = {
    "ACCUMULATED_DELAY": (
        "Накопленное опоздание",
        "ТС уже отстаёт от графика и продолжает терять время — задержка переносится "
        "на следующие остановки (эффект домино).",
    ),
    "CONGESTION": (
        "Затор на перегоне",
        "Скорость на участке заметно ниже обычной для этого времени суток.",
    ),
    "LONG_DWELL": (
        "Долгая стоянка на остановке",
        "ТС простаивает на остановке дольше обычного: посадка, двери или неисправность.",
    ),
    "SHORT_LAYOVER": (
        "Мало запаса на конечной",
        "Опоздание больше запланированной стоянки — следующий рейс начнётся с задержкой.",
    ),
    "EARLY_RUNNING": (
        "Опережение графика",
        "ТС идёт раньше расписания: пассажиры на следующих остановках могут не успеть.",
    ),
    "GPS_LOSS": (
        "Нет данных от ТС",
        "Телеметрия не поступает; прогноз построен по последнему состоянию и расписанию.",
    ),
    "UNKNOWN": (
        "Причина не определена",
        "Модель видит риск задержки, но ни один типовой паттерн не подтвердился.",
    ),
}

_ACTIONS: dict[str, tuple[str, str]] = {
    "DRIVER_CONTACT": ("Связаться с водителем", "Уточнить обстановку и скорректировать режим."),
    "REDUCE_LAYOVER": (
        "Сократить стоянку на конечной",
        "Отправить ТС в следующий рейс раньше, чтобы вернуть его на график.",
    ),
    "RESERVE_VEHICLE": (
        "Резервный выпуск",
        "Подготовить подменное ТС на следующий рейс, чтобы не сорвать интервал.",
    ),
    "SIGNAL_PRIORITY": (
        "Приоритет на светофорах",
        "Запросить у ЦОДД приоритетный проезд на перегоне.",
    ),
    "DETOUR": ("Проверить объезд", "Оценить схему объезда затора и сообщить водителю."),
    "CHECK_STOP": (
        "Проверить остановку",
        "Посмотреть камеры и пассажиропоток: возможна давка или помеха подъезду.",
    ),
    "HOLD_AT_STOP": (
        "Придержать на остановке",
        "Задержать ТС на ближайшей остановке до планового времени.",
    ),
    "CHECK_EQUIPMENT": (
        "Проверить бортовой терминал",
        "Возможен сбой связи или навигации; запросить статус у водителя.",
    ),
    "MONITOR": ("Наблюдать", "Держать ТС на контроле, прогноз обновляется каждую минуту."),
}

_RECOMMENDED: dict[CauseCode, tuple[str, ...]] = {
    "ACCUMULATED_DELAY": ("REDUCE_LAYOVER", "RESERVE_VEHICLE", "DRIVER_CONTACT"),
    "CONGESTION": ("SIGNAL_PRIORITY", "DETOUR", "DRIVER_CONTACT"),
    "LONG_DWELL": ("DRIVER_CONTACT", "CHECK_STOP"),
    "SHORT_LAYOVER": ("RESERVE_VEHICLE", "REDUCE_LAYOVER"),
    "EARLY_RUNNING": ("HOLD_AT_STOP", "DRIVER_CONTACT"),
    "GPS_LOSS": ("CHECK_EQUIPMENT", "DRIVER_CONTACT"),
    "UNKNOWN": ("MONITOR",),
}

FEATURE_LABELS: dict[str, str] = {
    "cur_dev": "Отклонение на последней остановке",
    "dev_trend": "Тренд отклонения за 15 мин",
    "spd5": "Средняя скорость за 5 мин",
    "speed_ratio": "Скорость к обычной на перегоне",
    "stop5": "Доля стоянки за 5 мин",
    "dwell": "Стоянка на текущей остановке",
    "layover": "Запас на конечной",
    "stale": "Без данных",
    "dist_tgt": "Расстояние до целевой остановки",
}


def recommendations_for(code: CauseCode) -> list[Recommendation]:
    """Рекомендации диспетчеру для причины ``code``."""
    return [
        Recommendation(code=a, title=_ACTIONS[a][0], description=_ACTIONS[a][1])
        for a in _RECOMMENDED[code]
    ]


def make_cause(code: CauseCode, evidence: list[Evidence]) -> Cause:
    """Собирает причину с заголовком и пояснением из справочника."""
    title, details = CAUSES[code]
    return Cause(code=code, title=title, details=details, evidence=evidence)


def evidence(feature: str, value: str, contribution_s: float | None) -> Evidence:
    """Факт-обоснование с человекочитаемой подписью признака."""
    return Evidence(
        feature=feature,
        label=FEATURE_LABELS.get(feature, feature),
        value=value,
        contribution_s=contribution_s,
    )


def format_delay(seconds: float) -> str:
    """Форматирует задержку для людей: ``+2 мин 30 с``, ``−45 с``."""
    sign = "+" if seconds >= 0 else "−"
    total = int(round(abs(seconds)))
    minutes, secs = divmod(total, 60)
    if minutes == 0:
        return f"{sign}{secs} с"
    return f"{sign}{minutes} мин {secs} с" if secs else f"{sign}{minutes} мин"
