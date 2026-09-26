"""Время контракта: naive ISO-строки «времени датасета»."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

import pandas as pd


def fmt(t: datetime | pd.Timestamp) -> str:
    """``2026-01-06T07:14:00`` — формат ``NaiveTime`` контракта."""
    return t.strftime("%Y-%m-%dT%H:%M:%S")


def parse(s: str) -> datetime:
    """Обратное к :func:`fmt`."""
    return datetime.fromisoformat(s)


def from_epoch(ts: int) -> datetime:
    """NDTP ``timestamp`` → naive время датасета (INTERFACES: без часового пояса)."""
    return datetime(1970, 1, 1) + timedelta(seconds=int(ts))


def to_epoch(t: datetime) -> int:
    """Naive время датасета → NDTP ``timestamp``."""
    return calendar.timegm(t.timetuple())


def floor_minute(t: datetime) -> datetime:
    """Начало сим-минуты."""
    return t.replace(second=0, microsecond=0)


def utc_iso(epoch: float) -> str:
    """Wall-clock время (``time.time()``) в ISO UTC."""
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
