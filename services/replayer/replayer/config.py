"""Настройки replayer из переменных окружения (``docs/INTERFACES.md`` §2)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from pathlib import Path


def _flag(value: str) -> bool:
    """``"1"/"true"/"yes"/"on"`` → True."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _hostport(value: str) -> tuple[str, int]:
    """``"backend:9201"`` → ``("backend", 9201)``."""
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"BACKEND_NDTP должен быть host:port, получено {value!r}")
    return host, int(port)


@dataclass(frozen=True)
class Settings:
    """Параметры воспроизведения истории.

    Время в CSV наивное; в NDTP оно уходит как unix-секунды «как если бы это был
    UTC» — backend декодирует обратно тем же правилом.
    """

    data_dir: Path = Path("data/raw")
    ndtp_host: str = "backend"
    ndtp_port: int = 9201
    backend_http: str = "http://backend:8000"
    start: time = time(7, 0)
    speed: float = 30.0
    loop: bool = True
    loop_hours: float | None = None
    warmup_min: float = 30.0
    warmup_speed: float = 600.0
    autostart: bool = True
    fleet_multiplier: int = 1
    handshake_pause_s: float = 0.2
    heartbeat_s: float = 5.0
    tick_s: float = 0.1

    @property
    def traffic_csv(self) -> Path:
        """Путь к потоку validate-дня."""
        return self.data_dir / "validate" / "traffic.csv"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Читает настройки из окружения; отсутствующие — значения по умолчанию."""
        env = os.environ if env is None else env
        host, port = _hostport(env.get("BACKEND_NDTP", "backend:9201"))
        loop_hours = env.get("REPLAY_LOOP_HOURS")
        return cls(
            data_dir=Path(env.get("DATA_DIR", "data/raw")),
            ndtp_host=host,
            ndtp_port=port,
            backend_http=env.get("BACKEND_HTTP", "http://backend:8000").rstrip("/"),
            start=time.fromisoformat(env.get("REPLAY_START", "07:00")),
            speed=float(env.get("REPLAY_SPEED", "1")),
            loop=_flag(env.get("REPLAY_LOOP", "1")),
            loop_hours=float(loop_hours) if loop_hours else None,
            warmup_min=float(env.get("REPLAY_WARMUP_MIN", "30")),
            warmup_speed=float(env.get("WARMUP_SPEED", "600")),
            autostart=_flag(env.get("REPLAY_AUTOSTART", "1")),
            fleet_multiplier=max(1, int(env.get("FLEET_MULTIPLIER", "1"))),
        )
