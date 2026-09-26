"""Замер латентности ML-сервиса ``/predict`` на реальных признаках validate.

Запуск (сервис уже поднят)::

    uv run python -m scripts.bench_ml --url http://127.0.0.1:8001 --batches 30 300
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx
import numpy as np

from services.ml.app.dataset import load_split

REPEATS = 50


def _payload(n: int, model: str) -> dict:
    """Батч из ``n`` реальных точек validate (по кругу)."""
    val = load_split("validate")
    rows = val.x.to_dict(orient="records")
    items = []
    for i in range(n):
        feats = {k: (None if v != v else float(v)) for k, v in rows[i % len(rows)].items()}
        items.append({"id": f"bench:{i}", "features": feats})
    return {"model": model, "items": items}


def bench(url: str, n: int, model: str) -> dict:
    """p50/p95 серверной (``latency_ms``) и клиентской латентности, мс."""
    body = _payload(n, model)
    server, client = [], []
    with httpx.Client(base_url=url, timeout=30) as c:
        c.post("/predict", json=body).raise_for_status()
        for _ in range(REPEATS):
            start = time.perf_counter()
            r = c.post("/predict", json=body)
            client.append((time.perf_counter() - start) * 1000)
            r.raise_for_status()
            server.append(r.json()["latency_ms"])
    pct = lambda a, q: round(float(np.percentile(a, q)), 2)  # noqa: E731
    return {"batch": n, "model": model, "server_p50_ms": pct(server, 50),
            "server_p95_ms": pct(server, 95), "client_p50_ms": pct(client, 50),
            "client_p95_ms": pct(client, 95)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Латентность /predict ML-сервиса")
    p.add_argument("--url", default="http://127.0.0.1:8001")
    p.add_argument("--batches", type=int, nargs="+", default=[30, 300])
    p.add_argument("--model", default="stream")
    args = p.parse_args(argv)
    for n in args.batches:
        print(json.dumps(bench(args.url, n, args.model), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
