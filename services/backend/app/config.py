"""Настройки бэкенда из переменных окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VERSION = "0.1.0"
DATASET_DAY = datetime(2026, 1, 6)


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    """Параметры сервиса; значения по умолчанию подходят для локального запуска.

    Все пороги времени с суффиксом ``_sim_s`` — в сим-секундах, ``_wall_s`` — по
    настенным часам.
    """

    data_dir: Path = Path("data/raw")
    models_dir: Path = Path("models")
    ml_url: str = "http://localhost:8001"
    replayer_url: str = "http://localhost:8090"
    ndtp_host: str = "0.0.0.0"
    ndtp_port: int = 9201
    ndtp_enabled: bool = True
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    queue_size: int = 20_000
    history_min: int = 60
    warmup_min: int = 15
    stale_after_sim_s: float = 120.0
    remove_after_sim_s: float = 15 * 60.0
    unknown_remove_wall_s: float = 300.0
    degraded_after_wall_s: float = 30.0
    ml_timeout_s: float = 1.0
    ml_fail_threshold: int = 3
    ml_open_s: float = 15.0
    db_path: Path = Path("/tmp/transit_pulse_journal.sqlite")
    code_docs_dir: Path = Path("docs/sphinx/_build/html")

    @classmethod
    def from_env(cls) -> Settings:
        """Читает настройки из окружения (``DATA_DIR``, ``ML_URL``, ``NDTP_PORT`` …)."""
        cors = os.environ.get("CORS_ORIGINS", "*")
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", "data/raw")),
            models_dir=Path(os.environ.get("MODELS_DIR", "models")),
            ml_url=os.environ.get("ML_URL", "http://localhost:8001").rstrip("/"),
            replayer_url=os.environ.get("REPLAYER_URL", "http://localhost:8090").rstrip("/"),
            ndtp_host=os.environ.get("NDTP_HOST", "0.0.0.0"),
            ndtp_port=_env_int("NDTP_PORT", 9201),
            ndtp_enabled=os.environ.get("NDTP_ENABLED", "1") != "0",
            cors_origins=[c.strip() for c in cors.split(",") if c.strip()],
            queue_size=_env_int("INGEST_QUEUE", 20_000),
            stale_after_sim_s=_env_float("STALE_AFTER_SIM_S", 120.0),
            degraded_after_wall_s=_env_float("DEGRADED_AFTER_S", 30.0),
            ml_timeout_s=_env_float("ML_TIMEOUT_S", 1.0),
            db_path=Path(os.environ.get("JOURNAL_DB", "/tmp/transit_pulse_journal.sqlite")),
            code_docs_dir=Path(os.environ.get("CODE_DOCS_DIR", "docs/sphinx/_build/html")),
        )
