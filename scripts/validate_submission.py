"""Проверка ``submission.csv`` до загрузки на платформу.

Платформа принимает строго ``sample_id;prediction``: все ``sample_id`` из
``validate/points.csv``, без дублей и пропусков. Ошибочная загрузка тоже
расходует дневной лимит попыток, поэтому формат проверяем локально.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POINTS = REPO_ROOT / "data" / "raw" / "validate" / "points.csv"
HEADER = "sample_id;prediction"


def validate_submission(sub_path: Path, points_path: Path = DEFAULT_POINTS) -> list[str]:
    """Возвращает список ошибок формата; пустой список — файл можно загружать."""
    with open(sub_path, encoding="utf-8") as f:
        header = f.readline().strip().lstrip("﻿")
    if header != HEADER:
        return [f"заголовок должен быть '{HEADER}', а не '{header}'"]

    sub = pd.read_csv(sub_path, sep=";", dtype={"sample_id": str})
    expected = set(pd.read_csv(points_path, usecols=["sample_id"], dtype=str)["sample_id"])
    errors = []

    dups = sub["sample_id"][sub["sample_id"].duplicated()].unique()
    if len(dups):
        errors.append(f"дубли sample_id: {', '.join(dups[:5])}")
    missing = sorted(expected - set(sub["sample_id"]))
    if missing:
        errors.append(f"нет {len(missing)} sample_id, например: {', '.join(missing[:5])}")
    extra = sorted(set(sub["sample_id"]) - expected)
    if extra:
        errors.append(f"лишние sample_id: {', '.join(extra[:5])}")

    pred = pd.to_numeric(sub["prediction"], errors="coerce")
    if not np.isfinite(pred).all():
        errors.append(f"NaN/нечисловые prediction в {int((~np.isfinite(pred)).sum())} строках")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка формата submission.csv")
    parser.add_argument("submission", type=Path)
    parser.add_argument("--points", type=Path, default=DEFAULT_POINTS)
    args = parser.parse_args(argv)

    errors = validate_submission(args.submission, args.points)
    for err in errors:
        print(f"ОШИБКА: {err}", file=sys.stderr)
    if not errors:
        print(f"OK: {args.submission} готов к загрузке")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
