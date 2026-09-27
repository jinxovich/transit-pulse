"""Сабмит из потока: validate-день через настоящий путь backend → ML, модель ``stream``.

Телеметрия ``validate/traffic.csv`` уходит NDTP-кадрами по TCP в backend (как от replayer'а),
backend на каждой сим-минуте строит признаки на момент T и запрашивает ML-сервис
(:mod:`scripts.stream_eval_run`). Из того, что поток выдал, берутся прогнозы для точек
``validate/points.csv``: ключ — ТС, момент T и целевая остановка. Подсказку организаторов
``cur_dev_s`` поток не использует: отклонение считается онлайн по GPS.

Так результат на платформе подтверждается работающей потоковой системой, а не офлайн-скриптом.

Запуск (≈10 мин CPU)::

    uv run python -m scripts.make_submission_stream
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

from scripts.stream_eval_run import ROOT, RunConfig, run_stream
from scripts.validate_submission import validate_submission

OUT = ROOT / "submissions" / "sub_v4_stream.csv"
WARMUP = timedelta(minutes=60)
"""Поток стартует раньше первой точки: backend прогнозирует, когда у ТС накопилась история."""


def pick_predictions(points: pd.DataFrame, preds: pd.DataFrame) -> pd.DataFrame:
    """Прогноз потока для каждой точки validate по ключу (ТС, T, целевая остановка).

    Возвращает ``sample_id``, ``prediction`` и режим прогноза (``ml`` / ``fallback``);
    точки, для которых поток прогноза не выдал, — с ``NaN``.
    """
    p = preds.assign(T=pd.to_datetime(preds["T"]), visit_id=preds["visit_id"].astype("int64"))
    p = p.drop_duplicates(["tr_id", "T", "visit_id"], keep="last")
    m = points.merge(p[["tr_id", "T", "visit_id", "delay_s", "mode"]], how="left",
                     left_on=["tr_id", "T", "target_stop_id"],
                     right_on=["tr_id", "T", "visit_id"])  # fmt: skip
    return pd.DataFrame({"sample_id": m["sample_id"], "prediction": m["delay_s"].round(2),
                         "mode": m["mode"]})  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    points = pd.read_csv(ROOT / "data" / "raw" / "validate" / "points.csv", parse_dates=["T"])
    t0, t1 = points["T"].min().to_pydatetime(), points["T"].max().to_pydatetime()
    log = asyncio.run(run_stream(RunConfig(start=t0 - WARMUP, end=t1)))
    sub = pick_predictions(points, pd.DataFrame(log.preds))
    missing = int(sub["prediction"].isna().sum())
    if missing:
        print(f"поток не выдал прогноз для {missing} из {len(sub)} точек", file=sys.stderr)
        return 1
    sub[["sample_id", "prediction"]].to_csv(args.out, sep=";", index=False)
    problems = validate_submission(args.out)
    modes = sub["mode"].value_counts().to_dict()
    print(f"точек {len(sub)}, режимы прогноза {modes}, поток {log.wall_s:.0f} с")
    print(f"{args.out}: {'OK' if not problems else problems}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
