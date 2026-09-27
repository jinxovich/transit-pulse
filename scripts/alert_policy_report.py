"""Markdown-отчёт по подбору политики алертов (``docs/perf/alert_policy.md``)."""

from __future__ import annotations

from pathlib import Path

TOP_N = 10


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _num(v: float | None, nd: int = 2) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def _policy(p: dict) -> str:
    return (f"delay > {p['alert_delay_s']:g} c **{p['mode']}** p_late ≥ {p['alert_p_late']:g}, "
            f"серия N = {p['min_streak']}")  # fmt: skip


def _row(r: dict) -> str:
    return (f"| {_policy(r['policy'])} | {r['n_incidents']} | {_pct(r['precision'])} "
            f"| {_pct(r['trip_recall'])} | {_pct(r['visit_recall'])} "
            f"| {_num(r['alerts_per_vehicle_hour'])} | {_pct(r['lead_ge_10_share'])} "
            f"| {_num(r['lead_median_min'], 1)} | {_num(r['f1'], 3)} |")  # fmt: skip


HEADER = (
    "| Политика | Инцидентов | Точность | Полнота (рейсы) | Полнота (визиты) "
    "| Алертов / ТС·ч | Упреждение ≥ 10 мин | Медиана упреждения, мин | F1 |\n"
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
)


def _top(grid: list[dict]) -> list[dict]:
    """Топ компромиссов без дублей по метрикам (многие «or»-политики совпадают)."""
    seen, out = set(), []
    for r in grid:
        key = (r["n_incidents"], r["precision"], r["trip_recall"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out[:TOP_N]


def render(res: dict) -> str:
    cur, sel = res["current_honest"], res["selected"]
    online, rf = res["check"]["online"], res["check"]["replay_final"]
    floor = res["min_trip_recall"]
    met = res["selected_meets_recall_floor"]
    sel_note = (
        f"максимум F1 при полноте по рейсам ≥ {floor:.0%} и упреждении 100% ≥ 10 мин"
        if met
        else f"ни одна политика сетки не дала полноту по рейсам ≥ {floor:.0%} — взят "
        "максимум F1 без этого ограничения"
    )
    lines = [
        "# Политика алертов: реплей на честных прогнозах",
        "",
        "Реплей логики `IncidentBook` (`scripts/alert_replay.py`) на дампе потокового прогона "
        "за 06.01.2026 (`data/eval/stream_dump.pkl`, 13 ТС, "
        f"{cur['vehicle_hours']:.0f} ТС·ч в режиме готовности). Прогнозы — **честные** ответы "
        "фолд-моделей (`h_*`) с той же калибровкой интервалов и p_late, что у ML-сервиса "
        "(`models/calibration_stream.json`); факт `test/schedule.csv` используется только для "
        "закрытия инцидентов и оценки.",
        "",
        "Метрики: точность — доля инцидентов с фактической задержкой цели > 120 c; полнота по "
        "рейсам — доля опоздавших рейсов (≥ 1 визит с фактом > 120 c среди визитов, побывавших "
        f"в окне прогноза; всего {cur['n_late_trips']}) с инцидентом на этом рейсе; полнота по "
        f"визитам — то же по визитам ({cur['n_late_visits']}); F1 — по точности и полноте по "
        "рейсам. Серия N — сколько проходов (сим-минут) подряд визит окна удовлетворял условию.",
        "",
        "## Проверка реплея",
        "",
        f"Текущая политика на прогнозах **финальной** модели: реплей — инцидентов "
        f"{rf['n_incidents']}, точность {_pct(rf['precision'])}; онлайн-прогон — инцидентов "
        f"{online['n_incidents']}, точность {_pct(online['precision'])}. Общих целей "
        f"{online['same_target_in_replay']}, из них открыты в ту же сим-минуту "
        f"{online['same_target_and_minute']}. Расхождение — из-за закрытия: онлайн — по "
        "виртуальному прибытию из GPS-трека, в реплее — по факту расписания.",
        "",
        "## Текущая vs выбранная (честные прогнозы)",
        "",
        HEADER,
        _row(cur).replace("| delay", "| Текущая: delay", 1),
        _row(sel).replace("| delay", "| **Выбранная**: delay", 1),
        "",
        f"Критерий выбора: {sel_note}; при равенстве — меньше алертов на ТС·ч.",
        "",
        "## Топ-10 компромиссов",
        "",
        "Сетка: delay ∈ {120, 150, 180} c × p_late ∈ {0.5, 0.6, 0.7} × {or, and} × N ∈ {1, 2, 3}. "
        "Честный p_late — это P(delay > 120) из квантилей, поэтому «delay > 120 or p ≥ x» при "
        "x ≥ 0.5 совпадает с «p ≥ 0.5»: одинаковые строки схлопнуты.",
        "",
        HEADER,
        *(_row(r) for r in _top(res["grid"])),
        "",
        "## Вывод",
        "",
        _conclusion(cur, sel),
        "",
    ]
    return "\n".join(lines)


def _conclusion(cur: dict, sel: dict) -> str:
    return (
        f"Серия из {sel['policy']['min_streak']} проходов и чуть более строгий порог "
        f"отсекают «мигающие» красные прогнозы: алертов на ТС·ч "
        f"{_num(cur['alerts_per_vehicle_hour'])} → {_num(sel['alerts_per_vehicle_hour'])}, "
        f"точность {_pct(cur['precision'])} → {_pct(sel['precision'])} при полноте по рейсам "
        f"{_pct(cur['trip_recall'])} → {_pct(sel['trip_recall'])}; упреждение по-прежнему 100% "
        "≥ 10 мин. Выборка мала (один день, 13 ТС) и политика подобрана на ней же — разница "
        "между верхними строками топа в пределах шума, поэтому пороги вынесены в env "
        "(`ALERT_*`)."
    )


def write_report(res: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(res), "utf-8")
