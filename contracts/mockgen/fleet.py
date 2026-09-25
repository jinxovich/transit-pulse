"""Флот для моков: реальные траектории + синтетические сценарии задержек."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from contracts.mockgen.common import (
    ACTIVE_WINDOW,
    DEGRADED,
    GPS_LOSS,
    MODEL_VERSION,
    N_FRAMES,
    SIM_START,
    STALE_AFTER,
    UNKNOWN_UNITS,
    WARMUP_FRAMES,
    fmt,
    frame_time,
)
from transit_core import schemas as S

HORIZON = (timedelta(minutes=10), timedelta(minutes=15))


@dataclass(frozen=True)
class Scenario:
    """Синтетическая динамика задержки ТС: база + тренд + волна."""

    name: str
    base: float
    slope: float  # секунд задержки за сим-минуту
    cause: S.CauseCode
    phase: float = 0.0
    amplitude: float = 12.0

    def delay(self, t: datetime) -> float:
        minutes = max((t - SIM_START).total_seconds() / 60, -5.0)
        wave = self.amplitude * math.sin(minutes / 3 + self.phase)
        return self.base + self.slope * minutes + wave


SCENARIOS = (
    Scenario("congestion", base=40, slope=9, cause="CONGESTION"),
    Scenario("domino", base=150, slope=3, cause="ACCUMULATED_DELAY", phase=1.0),
    Scenario("yellow", base=85, slope=0, cause="LONG_DWELL", amplitude=25),
    Scenario("early", base=-95, slope=-1, cause="EARLY_RUNNING", phase=2.0),
    Scenario("gps_loss", base=30, slope=0, cause="GPS_LOSS"),
)


def green_scenario(rng: random.Random) -> Scenario:
    return Scenario(
        "green", base=rng.uniform(0, 40), slope=0.3, cause="UNKNOWN", phase=rng.uniform(0, 6)
    )


@dataclass
class VehicleCtx:
    """Всё, что генератору нужно знать об одном ТС."""

    vehicle_id: str
    unit_id: int
    kind: S.VehicleKind
    track: pd.DataFrame | None
    plan: pd.DataFrame | None = None
    scenario: Scenario | None = None
    route_id: str | None = None
    route_name: str | None = None
    walk: list[tuple[float, float, float]] = field(default_factory=list)


def _random_walk(i: int, rng: random.Random) -> list[tuple[float, float, float]]:
    lon, lat, heading = 37.58 + 0.03 * i, 55.74 + 0.01 * i, rng.uniform(0, 360)
    walk = []
    for _ in range(N_FRAMES):
        heading = (heading + rng.uniform(-20, 20)) % 360
        lon += 0.0006 * math.sin(math.radians(heading))
        lat += 0.0004 * math.cos(math.radians(heading))
        walk.append((lon, lat, heading))
    return walk


def build_vehicles(
    plan: pd.DataFrame, tracks: dict[int, pd.DataFrame], network: S.Network, rng: random.Random
) -> list[VehicleCtx]:
    """ТС датасета, активные в окне сессии, плюс неопознанные борта (как от эмулятора)."""
    route_names = {r.route_id: r.name for r in network.routes}
    end = frame_time(N_FRAMES)
    ctxs = []
    for tr_id, track in sorted(tracks.items()):
        in_window = track[(track["et"] >= SIM_START - ACTIVE_WINDOW) & (track["et"] <= end)]
        if in_window.empty:
            continue
        ctx = VehicleCtx(str(tr_id), int(track["unit_id"].iloc[0]), "no_schedule", track)
        vplan = plan[plan["tr_id"] == tr_id]
        if len(vplan):
            ctx.kind, ctx.plan, ctx.route_id = "scheduled", vplan, f"r{tr_id}"
            ctx.route_name = route_names[ctx.route_id]
        ctxs.append(ctx)
    _assign_scenarios([c for c in ctxs if c.kind == "scheduled"], rng)
    for i, unit in enumerate(UNKNOWN_UNITS):
        ctxs.append(VehicleCtx(f"u:{unit}", unit, "unknown", None, walk=_random_walk(i, rng)))
    return ctxs


def _assign_scenarios(scheduled: list[VehicleCtx], rng: random.Random) -> None:
    """Особые сценарии — самым загруженным в окне сессии ТС, чтобы у них были прогнозы."""
    lo, hi = SIM_START, frame_time(N_FRAMES) + timedelta(minutes=15)

    def busy(c: VehicleCtx) -> int:
        return int(((c.plan["tb"] > lo) & (c.plan["tb"] <= hi)).sum())

    for i, ctx in enumerate(sorted(scheduled, key=busy, reverse=True)):
        ctx.scenario = SCENARIOS[i] if i < len(SCENARIOS) else green_scenario(rng)


def stop_ref(row) -> S.StopRef:
    """Плановое прибытие из строки расписания."""
    return S.StopRef(
        visit_id=str(row.tt_action_item_id),
        stop_key=row.stop_key,
        name=row.building_address,
        lon=row.lon,
        lat=row.lat,
        planned_at=fmt(row.tb),
    )


def risk_of(delay: float, p_late: float, th: S.Thresholds | None = None) -> S.RiskLevel:
    """Уровень риска по порогам контракта."""
    th = th or S.Thresholds()
    if delay > th.red_delay_s or p_late >= th.red_p_late:
        return "red"
    if delay < th.early_delay_s:
        return "early"
    if delay >= th.yellow_delay_s or p_late >= th.yellow_p_late:
        return "yellow"
    return "green"


def _target(plan: pd.DataFrame, t: datetime):
    """Первая остановка в окне (T+10, T+15]; иначе ближайшая после — вне окна."""
    lo, hi = t + HORIZON[0], t + HORIZON[1]
    window = plan[(plan["tb"] > lo) & (plan["tb"] <= hi)]
    if len(window):
        return next(window.itertuples()), True
    later = plan[(plan["tb"] > hi) & (plan["tb"] <= t + timedelta(hours=1))]
    return (next(later.itertuples()), False) if len(later) else (None, False)


def predict(ctx: VehicleCtx, t: datetime, fallback: bool) -> S.Prediction | None:
    """Синтетический прогноз, устроенный как настоящий: окно, интервал, риск."""
    target, horizon_ok = _target(ctx.plan, t)
    if target is None:
        return None
    d = round(ctx.scenario.delay(t), 1)
    p_late = round(1 / (1 + math.exp(-(d - 120) / 35)), 3)
    spread = 35 + 0.15 * abs(d)
    gps_loss = fallback and ctx.scenario.name == "gps_loss"
    return S.Prediction(
        target_stop=stop_ref(target),
        predicted_delay_s=d,
        p_late=p_late,
        interval_s=(round(d - spread, 1), round(d + spread + 10, 1)),
        expected_abs_error_s=round(38 + 0.12 * abs(d), 1),
        lead_min=round((target.tb - t).total_seconds() / 60, 2),
        generated_at=fmt(t),
        horizon_ok=horizon_ok,
        model_version=MODEL_VERSION,
        model_mode="fallback" if fallback else "ml",
        risk_level=risk_of(d, p_late),
        cause_code="GPS_LOSS" if gps_loss else ctx.scenario.cause,
    )


def _data_until(ctx: VehicleCtx, t: datetime, frame: int) -> datetime:
    """До какого момента ТС «прислало» данные: в обрывах позиция замирает."""
    if frame in DEGRADED:
        return frame_time(DEGRADED.start)
    if ctx.scenario and ctx.scenario.name == "gps_loss" and frame in GPS_LOSS:
        return frame_time(GPS_LOSS.start)
    return t


def _unknown_state(ctx: VehicleCtx, t: datetime, frame: int) -> S.VehicleState:
    lon, lat, heading = ctx.walk[frame]
    return S.VehicleState(
        vehicle_id=ctx.vehicle_id, kind="unknown", tr_id=None, unit_id=ctx.unit_id,
        route_id=None, route_name=None, lon=lon, lat=lat, heading=heading, speed_kmh=28.0,
        last_seen=fmt(t), stale=frame in DEGRADED, warming_up=False, current_dev_s=None,
        next_stop=None, prediction=None, risk_level="none", open_incident_id=None,
    )  # fmt: skip


def vehicle_state(ctx: VehicleCtx, frame: int) -> S.VehicleState | None:
    """Состояние ТС в кадре или None, если ТС сейчас не активно."""
    t = frame_time(frame)
    if ctx.kind == "unknown":
        return _unknown_state(ctx, t, frame)
    seen = ctx.track[ctx.track["et"] <= _data_until(ctx, t, frame)]
    if seen.empty or t - seen["et"].iloc[-1] > ACTIVE_WINDOW:
        return None
    p = seen.iloc[-1]
    stale = bool(t - p["et"] > STALE_AFTER)
    prediction = next_stop = dev = None
    if ctx.kind == "scheduled":
        prediction = predict(ctx, t, fallback=stale or frame in DEGRADED)
        upcoming = ctx.plan[ctx.plan["tb"] > t]
        next_stop = stop_ref(next(upcoming.itertuples())) if len(upcoming) else None
        dev = round(0.85 * ctx.scenario.delay(t), 1)
    return S.VehicleState(
        vehicle_id=ctx.vehicle_id, kind=ctx.kind, tr_id=ctx.vehicle_id, unit_id=ctx.unit_id,
        route_id=ctx.route_id, route_name=ctx.route_name, lon=float(p["lon"]),
        lat=float(p["lat"]), heading=float(p["heading"]), speed_kmh=float(p["speed"]),
        last_seen=fmt(p["et"]), stale=stale, warming_up=frame < WARMUP_FRAMES,
        current_dev_s=dev, next_stop=next_stop, prediction=prediction,
        risk_level=prediction.risk_level if prediction else "none", open_incident_id=None,
    )  # fmt: skip
