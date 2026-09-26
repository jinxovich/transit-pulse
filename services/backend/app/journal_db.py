"""Журнал прогнозов и инцидентов в SQLite (для разбора качества после прогона).

Пишется только из event loop, одной транзакцией на проход прогнозов. Путь —
``JOURNAL_DB`` (по умолчанию ``/tmp/transit_pulse_journal.sqlite``); если файл
открыть нельзя, журнал молча выключается — на работу сервиса он не влияет.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from transit_core import schemas as S

log = logging.getLogger(__name__)
SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    session_id TEXT, vehicle_id TEXT, visit_id TEXT, generated_at TEXT, planned_at TEXT,
    lead_min REAL, predicted_delay_s REAL, p_late REAL, q10 REAL, q90 REAL,
    horizon_ok INTEGER, model_mode TEXT, model_version TEXT, risk_level TEXT, cause_code TEXT
);
CREATE TABLE IF NOT EXISTS incident_events (
    session_id TEXT, kind TEXT, incident_id TEXT, sim_time TEXT, body TEXT
);
"""


class JournalDb:
    """Append-only журнал; все ошибки SQLite логируются и отключают журнал."""

    def __init__(self, path: Path) -> None:
        self.conn: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.executescript(SCHEMA)
        except (OSError, sqlite3.Error) as exc:
            log.warning("Журнал SQLite %s отключён: %s", path, exc)
            self.conn = None

    def _write(self, sql: str, rows: list[tuple]) -> None:
        if self.conn is None or not rows:
            return
        try:
            with self.conn:
                self.conn.executemany(sql, rows)
        except sqlite3.Error as exc:
            log.warning("Журнал SQLite отключён: %s", exc)
            self.conn = None

    def predictions(self, session_id: str, rows: list[tuple[str, S.Prediction]]) -> None:
        """Прогнозы прохода: ``[(vehicle_id, prediction)]``."""
        data = [
            (session_id, vid, p.target_stop.visit_id, p.generated_at, p.target_stop.planned_at,
             p.lead_min, p.predicted_delay_s, p.p_late, p.interval_s[0], p.interval_s[1],
             int(p.horizon_ok), p.model_mode, p.model_version, p.risk_level, p.cause_code)
            for vid, p in rows
        ]  # fmt: skip
        self._write("INSERT INTO predictions VALUES (" + ",".join("?" * 15) + ")", data)

    def incident(self, session_id: str, kind: str, inc: S.Incident, sim_time: str) -> None:
        """Событие жизненного цикла инцидента (тело — JSON контракта)."""
        row = (session_id, kind, inc.id, sim_time, inc.model_dump_json())
        self._write("INSERT INTO incident_events VALUES (?,?,?,?,?)", [row])

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
