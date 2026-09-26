"""Горизонт и точность НА ПОТОКЕ: validate-день через backend, сверка с ``labels_test``.

Проигрывает ``validate/traffic.csv`` через настоящий путь NDTP → ingest → state →
pipeline → ML (всё в одном процессе, инжектируемые часы, без ``sleep``) и для каждой
точки ``labels_test`` находит прогноз, который backend выдал в сим-минуту ``T`` для
целевой остановки. Считает покрытие, инвариант горизонта, MAE против baseline'ов,
паритет офлайн/поток, качество алертов и латентность прохода.

Пример (весь день, ≈5 мин)::

    uv run python -m scripts.stream_eval
    uv run python -m scripts.stream_eval --start 06:00 --end 09:00 --tr 131672 --tr 134040
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, time
from pathlib import Path

import pandas as pd

from scripts import stream_eval_metrics as M
from scripts.stream_eval_honest import honest_accuracy
from scripts.stream_eval_run import ROOT, RunConfig, StreamLog, run_stream

DAY = datetime(2026, 1, 6)
OUT_JSON = ROOT / "docs" / "perf" / "stream_eval.json"
OUT_MD = ROOT / "docs" / "perf" / "stream_eval.md"


def evaluate(cfg: RunConfig, honest: bool = True) -> tuple[dict, pd.DataFrame]:
    """Прогон + все метрики; возвращает отчёт и таблицу сопоставления с разметкой."""
    labels = M.load_labels(cfg.data_dir, cfg.start, cfg.end, cfg.tr_ids)
    log = asyncio.run(run_stream(cfg, M.label_keys(labels)))
    return report(cfg, labels, log, honest)


def report(
    cfg: RunConfig, labels: pd.DataFrame, log: StreamLog, honest: bool = True
) -> tuple[dict, pd.DataFrame]:
    """Метрики по журналу прогона (``honest`` — ещё и фолд-модели, ≈2 мин CPU)."""
    preds = pd.DataFrame(log.preds)
    minutes = pd.DataFrame(
        log.vehicle_minutes,
        columns=["tr_id", "T", "stale", "ready", "n_visits", "horizon_ok", "cur_dev"],
    )
    m = M.match(labels, preds, minutes)
    m = M.offline_stream(m, cfg.models_dir)
    out = {
        "run": {
            "start": cfg.start.isoformat(), "end": cfg.end.isoformat(), "speed": cfg.speed,
            "tr_ids": sorted(cfg.tr_ids) if cfg.tr_ids else "all",
            "path": "validate/traffic.csv → NDTP (replayer.source) → TCP IngestServer → "
                    "apply_frame → PipelineRunner.run_pass → ML serve (ASGI) → IncidentBook",
            "ingest": log.ingest,
        },
        "coverage": M.coverage(m),
        "horizon": M.horizon(preds, m),
        "accuracy": M.accuracy(m),
        "parity": M.parity(m, log.features, cfg.models_dir),
        "alerts": M.alerts(log.incidents, m, cfg.data_dir),
        "latency": M.latency(pd.DataFrame(log.passes), log.wall_s),
    }  # fmt: skip
    if honest:
        out["accuracy_honest"] = honest_accuracy(m, log.features, cfg.models_dir)
    return out, m


def _pct(v) -> str:
    return "—" if v is None else f"{100 * v:.1f}%"


def _table(rep: dict) -> list[tuple[str, str]]:
    c, h, a, p = rep["coverage"], rep["horizon"], rep["accuracy"], rep["parity"]
    al, lat, hon = rep["alerts"], rep["latency"], rep.get("accuracy_honest", {})
    to_fact = h["labels_minutes_from_forecast_to_actual_arrival"]
    red = al["points_red_vs_late"]
    hon_late = hon.get("points_pred_gt_120_vs_late_honest", {})
    return [
        ("Покрытие точек разметки прогнозом цели в момент T",
         f"{c['n_found']} / {c['n_labels']} ({_pct(c['share'])})"),
        ("Цель — первый визит окна (как у карточки ТС)", _pct(c["target_is_first_in_window"])),
        ("Прогнозов на потоке всего / в горизонте", f"{h['n_predictions']} / {h['n_horizon_ok']}"),
        ("`lead_min ∈ (10, 15]` у прогнозов в горизонте",
         _pct(h["horizon_ok_lead_in_window_share"])),
        ("`generated_at < planned_at` (нет «задним числом»)",
         _pct(h["generated_before_planned_share"])),
        ("Прогноз раньше фактического прибытия (метки)",
         f"{_pct(h['labels_forecast_before_actual_share'])}, мин. {to_fact.get('min')} мин, "
         f"медиана {to_fact.get('p50')} мин"),
        ("MAE stream-модели на потоке, честно (фолд-модели)",
         f"{hon.get('mae_stream_model_honest', '—')} с"),
        ("MAE финальной stream-модели (видела test при обучении)", f"{a['mae_stream_model']} с"),
        ("MAE baseline `cur_dev_s` (подсказка организаторов)", f"{a['mae_baseline_cur_dev_s']} с"),
        ("MAE baseline онлайн `cur_dev` (GPS)", f"{a['mae_baseline_online_cur_dev']} с"),
        ("MAE «задержки нет»", f"{a['mae_zero']} с"),
        ("Паритет офлайн/поток: средняя / макс. \\|Δ\\| прогноза",
         f"{p.get('mean_abs_diff_s')} / {p.get('max_abs_diff_s')} с"),
        ("Инцидентов / с упреждением ≥ 10 мин",
         f"{al['incidents_opened']} / {_pct(al['lead_ge_10_share'])}"),
        ("Precision инцидентов по факту расписания",
         _pct(al["incident_precision_vs_schedule_fact"])),
        ("Опоздавшие рейсы (метки) с инцидентом", _pct(al["late_trips_with_incident"])),
        ("Точки, честно: прогноз > 120 с vs `late` — precision / recall",
         f"{_pct(hon_late.get('precision'))} / {_pct(hon_late.get('recall'))}"),
        ("Точки, финальная модель: red vs `late` — precision / recall",
         f"{_pct(red['precision'])} / {_pct(red['recall'])}"),
        ("Проход на сим-минуту p50 / p95",
         f"{lat['pass_ms'].get('p50')} / {lat['pass_ms'].get('p95')} мс"),
        ("ML-батч p50 / p95", f"{lat['ml_ms'].get('p50')} / {lat['ml_ms'].get('p95')} мс"),
    ]  # fmt: skip


def render_md(rep: dict) -> str:
    """Короткая сводка для ``docs/perf/stream_eval.md``."""
    run, c, h, p = rep["run"], rep["coverage"], rep["horizon"], rep["parity"]
    lat = rep["latency"]
    rows = "\n".join(f"| {k} | {v} |" for k, v in _table(rep))
    hist = ", ".join(f"{k}: {v}" for k, v in h["lead_hist_by_minute"].items())
    feats = ", ".join(f"`{k}` ({v['n_mismatch']})"
                      for k, v in p.get("features_mismatch", {}).items()) or "нет"  # fmt: skip
    return f"""# Горизонт и точность на потоке (`scripts/stream_eval.py`)

Прогон {run["start"]} → {run["end"]} по пути `{run["path"]}`.
Кадров NDTP: {run["ingest"]["frames_sent"]}, потерь {run["ingest"]["dropped"]},
CRC-ошибок {run["ingest"]["crc_errors"]}. Сверка — с реальной разметкой `labels_test`
(тот же день и те же ТС, что в `validate`; факт используется только для оценки).

| Что | Значение |
|---|---|
{rows}

Распределение `lead_min` прогнозов в горизонте: {hist}.
Промахи покрытия: {c["miss_reasons"] or "нет"}. Расхождения признаков офлайн/поток: {feats}.
Когда цель не первая в окне, у неё то же плановое время, что у соседнего визита (две
остановки в одну минуту плана): прогноз есть для обеих, различается только порядок.

Проход прогнозов выполняется на каждой сим-минуте (сессия ×{run["speed"]:g}, как у replayer'а).
При p95 прохода {lat["pass_ms"].get("p95")} мс планировщик без пропуска минут держит скорость
до ×{lat["max_speed_without_skips"]}; режимы проходов: {lat["modes"]}.

**Как читать MAE.** Финальная stream-модель обучена на train+test, поэтому её MAE на
`labels_test` занижен. Честная цифра — фолд-модели той же схемы, что CV обучения: каждая
точка прогнозируется моделью, которая её не видела, на признаках, снятых на потоке.
Baseline'ы честные в обоих случаях. Инциденты и строка «финальная модель» тоже считаются
финальной моделью, поэтому оптимистичны; честный ориентир для алертов — строка «честно».
"""


def _hhmm(s: str) -> datetime:
    return datetime.combine(DAY.date(), time.fromisoformat(s))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="00:00", help="начало сим-окна, ЧЧ:ММ")
    ap.add_argument("--end", default="23:59", help="конец сим-окна, ЧЧ:ММ")
    ap.add_argument("--speed", type=float, default=30.0, help="скорость сессии (как REPLAY_SPEED)")
    ap.add_argument("--tr", type=int, action="append", help="только эти tr_id (можно несколько)")
    ap.add_argument("--no-honest", action="store_true", help="без фолд-моделей (быстрее)")
    ap.add_argument("--out", type=Path, default=OUT_JSON)
    ap.add_argument("--md", type=Path, default=OUT_MD)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    cfg = RunConfig(_hhmm(args.start), _hhmm(args.end), args.speed,
                    frozenset(args.tr) if args.tr else None)  # fmt: skip
    rep, _ = evaluate(cfg, honest=not args.no_honest)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), "utf-8")
    args.md.write_text(render_md(rep), "utf-8")
    print(json.dumps({k: rep[k] for k in ("coverage", "accuracy")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
