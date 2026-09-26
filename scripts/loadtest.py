"""Нагрузочный тест потока: replayer ×k бортов на повышенной скорости.

Перезапускает replayer в docker compose с ``FLEET_MULTIPLIER`` и ``REPLAY_SPEED``, затем
раз в ``--every`` секунд снимает ``/api/v1/metrics/summary`` и ``/api/v1/ingest/stats``
backend и печатает сводку: пакетов в секунду, лаг очереди, потери, латентности
ingest→state, прогноз→WS и батча ML. Результат пишется в JSON для ``docs/PERFORMANCE.md``.

Пример::

    uv run python -m scripts.loadtest --fleet 10 --speed 60 --duration 120
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "docs" / "perf" / "loadtest.json"


def restart_replayer(fleet: int, speed: float) -> None:
    """Пересоздаёт контейнер replayer с новым размером парка и скоростью."""
    env = {**os.environ, "FLEET_MULTIPLIER": str(fleet), "REPLAY_SPEED": str(speed)}
    subprocess.run(
        ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "replayer"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def sample(client: httpx.Client, api: str) -> dict:
    """Один замер метрик backend."""
    summary = client.get(f"{api}/metrics/summary").json()
    ingest = client.get(f"{api}/ingest/stats").json()
    return {
        "t": round(time.time(), 1),
        "pps": ingest["pps"],
        "packets_total": ingest["packets_total"],
        "connections": ingest["connections"],
        "queue_lag": summary["queue_lag"],
        "dropped": summary["dropped_packets"],
        "ingest_to_state_p95_ms": summary["ingest_to_state"]["p95_ms"],
        "pass_to_ws_p95_ms": summary["pass_to_ws"]["p95_ms"],
        "ml_batch_p95_ms": summary["ml_batch"]["p95_ms"],
        "vehicles_online": summary["kpis"]["vehicles_online"],
    }


def summarize(samples: list[dict]) -> dict:
    """Итог прогона: пики и последние значения латентностей."""
    steady = samples[len(samples) // 3 :] or samples
    last = samples[-1]
    return {
        "pps_avg": round(sum(s["pps"] for s in steady) / len(steady), 1),
        "pps_max": max(s["pps"] for s in samples),
        "connections": last["connections"],
        "vehicles_online": last["vehicles_online"],
        "queue_lag_max": max(s["queue_lag"] for s in samples),
        "dropped_total": last["dropped"],
        "ingest_to_state_p95_ms": last["ingest_to_state_p95_ms"],
        "pass_to_ws_p95_ms": last["pass_to_ws_p95_ms"],
        "ml_batch_p95_ms": last["ml_batch_p95_ms"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000/api/v1")
    parser.add_argument("--fleet", type=int, default=10, help="во сколько раз клонировать парк")
    parser.add_argument("--speed", type=float, default=60, help="сим-секунд в секунду")
    parser.add_argument("--duration", type=int, default=120, help="длительность замера, с")
    parser.add_argument("--every", type=float, default=5.0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-restart", action="store_true", help="не трогать replayer")
    args = parser.parse_args(argv)

    if not args.no_restart:
        restart_replayer(args.fleet, args.speed)
    samples: list[dict] = []
    with httpx.Client(timeout=5) as client:
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            time.sleep(args.every)
            try:
                s = sample(client, args.api)
            except (httpx.HTTPError, KeyError) as exc:
                print(f"замер пропущен: {exc}", file=sys.stderr)
                continue
            samples.append(s)
            print(
                f"pps={s['pps']:>7} conn={s['connections']:>4} lag={s['queue_lag']:>4} "
                f"drop={s['dropped']:>4} ingest p95={s['ingest_to_state_p95_ms']} мс "
                f"ws p95={s['pass_to_ws_p95_ms']} мс ml p95={s['ml_batch_p95_ms']} мс"
            )
    if not samples:
        print("backend не ответил ни разу", file=sys.stderr)
        return 1
    report = {"fleet": args.fleet, "speed": args.speed, **summarize(samples), "samples": samples}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
