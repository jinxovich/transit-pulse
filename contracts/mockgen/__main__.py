"""Точка входа генератора моков: ``uv run python -m contracts.mockgen``."""

from __future__ import annotations

import json
import random
import sys
import typing
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from contracts.mockgen.common import (
    DETAIL_FRAME,
    N_FRAMES,
    OUT,
    SESSION_ID,
    SIM_SPEED,
    fmt,
    frame_time,
    load_plan,
    load_tracks,
    ws,
)
from contracts.mockgen.details import segments_risk, vehicle_detail
from contracts.mockgen.fleet import VehicleCtx, build_vehicles, vehicle_state
from contracts.mockgen.incidents import IncidentBook
from contracts.mockgen.metrics import (
    ingest_stats,
    kpis,
    metrics_quality,
    metrics_summary,
    mode_of,
    system_status,
)
from contracts.mockgen.network import build_network
from transit_core import schemas as S

SEED = 7
# 'miss' — пропуск без алерта; в сценариях моков его нет, фронт рисует его как false_alarm.
NOT_COVERED = {("outcome", "miss")}
EXPECTED_ENUMS = {
    "risk": S.RiskLevel,
    "kind": S.VehicleKind,
    "model_mode": S.ModelMode,
    "status": S.IncidentStatus,
    "outcome": S.IncidentOutcome,
}


def dump(path: Path, obj: BaseModel | list[BaseModel] | dict) -> None:
    """Пишет модель или список моделей контракта в JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, list):
        data = [o.model_dump(mode="json") for o in obj]
    elif isinstance(obj, BaseModel):
        data = obj.model_dump(mode="json")
    else:
        data = obj
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def _note(cov: dict, states: dict[str, S.VehicleState], frame: int) -> None:
    cov["mode"].add(mode_of(frame))
    for s in states.values():
        cov["risk"].add(s.risk_level)
        cov["kind"].add(s.kind)
        cov["flag"] |= {f for f in ("stale", "warming_up") if getattr(s, f)}
        if s.prediction:
            cov["model_mode"].add(s.prediction.model_mode)
            cov["flag"].add("horizon_ok" if s.prediction.horizon_ok else "horizon_off")


def _check_coverage(cov: dict, book: IncidentBook) -> list[str]:
    cov["status"] = book.seen["status"]
    cov["outcome"] = book.seen["outcome"]
    missing = [
        f"{key}={value}"
        for key, alias in EXPECTED_ENUMS.items()
        for value in typing.get_args(alias)
        if value not in cov[key] and (key, value) not in NOT_COVERED
    ]
    missing += [m for m in ("LIVE", "WARMING_UP", "DEGRADED") if m not in cov["mode"]]
    missing += [f for f in ("stale", "warming_up", "horizon_off") if f not in cov["flag"]]
    return missing


def _write_rest(
    ctxs: dict[str, VehicleCtx],
    states: dict[str, S.VehicleState],
    frame: int,
    book: IncidentBook,
    network: S.Network,
) -> None:
    """REST-фикстуры: срез состояния на кадре DETAIL_FRAME."""
    t = frame_time(frame)
    n_units = sum(s.kind != "unknown" for s in states.values())
    k = kpis(states, book, frame)
    clock = S.SimClock(sim_time=fmt(t), session_id=SESSION_ID, speed=SIM_SPEED, state="running")
    health = S.Health(status="ok", version="0.1.0", checks={"ingest": "ok", "ml": "ok"})
    live = next(s for s in states.values() if s.kind == "scheduled" and not s.stale)
    snapshot = {
        "vehicles": list(states.values()),
        "incidents": list(book.all.values()),
        "kpis": k,
        "status": system_status(frame, n_units),
    }
    dump(OUT / "config.json", S.AppConfig(sim_speed=SIM_SPEED, session_id=SESSION_ID))
    dump(OUT / "health.json", health)
    dump(OUT / "network.json", network)
    dump(OUT / "vehicles.json", list(states.values()))
    dump(OUT / "segments_risk.json", segments_risk(ctxs, states))
    dump(OUT / "metrics_summary.json", metrics_summary(k))
    dump(OUT / "sim_clock.json", clock)
    dump(OUT / "ingest_stats.json", ingest_stats(live, n_units))
    dump(OUT / "snapshot.json", ws("snapshot", t, snapshot))
    for vid, st in states.items():
        if st.kind == "scheduled":
            detail = vehicle_detail(ctxs[vid], st, frame, book)
            dump(OUT / "vehicle_details" / f"{vid}.json", detail)


def _frame_messages(
    ctxs: dict[str, VehicleCtx], book: IncidentBook, prev: dict, frame: int
) -> tuple[list[dict], dict[str, S.VehicleState]]:
    """WS-сообщения одного кадра и новое состояние флота."""
    t = frame_time(frame)
    states = {vid: s for vid, c in ctxs.items() if (s := vehicle_state(c, frame))}
    n_units = sum(s.kind != "unknown" for s in states.values())
    msgs = []
    if frame == 0 or mode_of(frame) != mode_of(frame - 1):
        msgs.append(ws("system.status", t, system_status(frame, n_units)))
    msgs += book.step(ctxs, states, frame)
    states = {
        vid: s.model_copy(update={"open_incident_id": book.open_by_vehicle.get(vid)})
        for vid, s in states.items()
    }
    changed = [s for vid, s in states.items() if prev.get(vid) != s]
    removed = [vid for vid in prev if vid not in states]
    if changed or removed:
        msgs.append(ws("vehicles.delta", t, {"vehicles": changed, "removed": removed}))
    if frame % 2 == 0:
        msgs.append(ws("kpis", t, kpis(states, book, frame)))
    if frame % 10 == 0:
        msgs.append(ws("ping", t, {}))
    return msgs, states


def run() -> list[str]:
    """Прогоняет сессию, пишет фикстуры; возвращает непокрытые значения enum'ов."""
    rng = random.Random(SEED)
    plan = load_plan()
    network = build_network(plan)
    ctxs = {c.vehicle_id: c for c in build_vehicles(plan, load_tracks(), network, rng)}
    book, prev, cov = IncidentBook(rng), {}, defaultdict(set)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "ws_session.jsonl", "w", encoding="utf-8") as f:
        for frame in range(N_FRAMES):
            msgs, prev = _frame_messages(ctxs, book, prev, frame)
            line = {"frame": frame, "sim_time": fmt(frame_time(frame)), "messages": msgs}
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            _note(cov, prev, frame)
            if frame == DETAIL_FRAME:
                _write_rest(ctxs, prev, frame, book, network)
    dump(OUT / "incidents.json", list(book.all.values()))
    dump(OUT / "metrics_quality.json", metrics_quality(book))
    return _check_coverage(cov, book)


def main() -> int:
    missing = run()
    files = sorted(p.relative_to(OUT).as_posix() for p in OUT.rglob("*.json*"))
    print(f"Фикстуры: {len(files)} файлов в {OUT}")
    if missing:
        print(f"НЕ покрыто сценариями: {', '.join(missing)}", file=sys.stderr)
        return 1
    print("Все значения enum'ов контракта покрыты")
    return 0


if __name__ == "__main__":
    sys.exit(main())
