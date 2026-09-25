"""KPI, статус системы, метрики производительности и качества для моков."""

from __future__ import annotations

import calendar
import math
import struct
from collections import Counter
from datetime import datetime

from contracts.mockgen.common import (
    DEGRADED,
    MODEL_VERSION,
    SIM_SPEED,
    STEP,
    VEHICLES_TOTAL,
    WARMUP_FRAMES,
)
from contracts.mockgen.incidents import IncidentBook
from transit_core import schemas as S

REASONS: dict[S.StreamMode, str | None] = {
    "WARMING_UP": "Прогрев: копится история телеметрии, инциденты пока не создаются",
    "LIVE": None,
    "DEGRADED": "Нет пакетов NDTP 30 с — прогноз по расписанию и последнему состоянию",
}
RISKS = ("green", "yellow", "red", "early", "none")


def mode_of(frame: int) -> S.StreamMode:
    if frame < WARMUP_FRAMES:
        return "WARMING_UP"
    return "DEGRADED" if frame in DEGRADED else "LIVE"


def system_status(frame: int, n_units: int) -> S.SystemStatus:
    mode = mode_of(frame)
    degraded = mode == "DEGRADED"
    outage_s = (frame - DEGRADED.start + 3) * STEP.total_seconds() / SIM_SPEED
    return S.SystemStatus(
        mode=mode,
        reason=REASONS[mode],
        last_packet_age_s=round(outage_s, 1) if degraded else 0.4,
        units_connected=0 if degraded else n_units,
        ml_status="ok",
        model_version=MODEL_VERSION,
        model_mode="fallback" if degraded else "ml",
    )


def kpis(states: dict[str, S.VehicleState], book: IncidentBook, frame: int) -> S.Kpis:
    by_risk = Counter(s.risk_level for s in states.values())
    preds = [s.prediction.predicted_delay_s for s in states.values() if s.prediction]
    created = list(book.all.values())
    online = sum(not s.stale for s in states.values())
    lead_ok = sum(i.lead_min >= 10 for i in created) / len(created) if created else None
    return S.Kpis(
        vehicles_online=online,
        vehicles_total=VEHICLES_TOTAL,
        by_risk=S.RiskCounts(**{k: by_risk.get(k, 0) for k in RISKS}),
        incidents_open=len(book.open_by_vehicle),
        avg_predicted_delay_s=round(sum(preds) / len(preds), 1) if preds else None,
        lead_ok_share=round(lead_ok, 3) if lead_ok is not None else None,
        e2e_latency_ms_p95=round(160 + 40 * math.sin(frame / 7), 1),
        ml_latency_ms_p95=round(11 + 2 * math.sin(frame / 5), 1),
        ingest_pps=0.0 if frame in DEGRADED else round(online * SIM_SPEED / 12, 1),
    )


def metrics_summary(k: S.Kpis) -> S.MetricsSummary:
    return S.MetricsSummary(
        kpis=k,
        ingest_to_state=S.LatencyStats(p50_ms=0.4, p95_ms=1.3, max_ms=6.8),
        pass_to_ws=S.LatencyStats(p50_ms=62.0, p95_ms=148.0, max_ms=310.0),
        ml_batch=S.LatencyStats(p50_ms=6.1, p95_ms=11.8, max_ms=24.0),
        queue_lag=0,
        dropped_packets=0,
    )


def metrics_quality(book: IncidentBook) -> S.QualityMetrics:
    leads = Counter(min(int(i.lead_min), 15) for i in book.all.values())
    return S.QualityMetrics(
        online_mae_s=61.2,
        n_resolved=sum(i.status == "resolved" for i in book.all.values()),
        lead_ok_share=1.0,
        lead_hist=[S.LeadBucket(lead_min=m, count=leads.get(m, 0)) for m in range(10, 16)],
        alert_precision=0.78,
        alert_recall=0.71,
        offline=S.OfflineMetrics(cv_mae_baseline_s=93.36, cv_mae_model_s=63.62, improvement=0.319),
    )


def _crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def sample_ndtp_frame(unit_id: int, ts: datetime, lon: float, lat: float, speed: int, course: int):
    """Кадр NDTP с одной ячейкой G6CellNav00 по спецификации организаторов.

    Нужен только для витрины «последний пакет» в моках; боевой кодек — в
    ``transit_core.ndtp``.
    """
    epoch = calendar.timegm(ts.timetuple())
    flags = 0b1110_0000  # bit5 N, bit6 E, bit7 координаты достоверны
    nav = struct.pack(
        "<IIIBBHHHHHBB",
        epoch, round(abs(lon) * 1e7), round(abs(lat) * 1e7), flags, 200, speed, speed + 5,
        course, 0, 150, 12, 9,
    )  # fmt: skip
    body = bytes([0, 0]) + nav
    nph = struct.pack("<HHHI", 1, 101, 1, 42)
    npl = (
        struct.pack("<HHH", 0x7E7E, len(nph) + len(body), 0)
        + struct.pack(">H", _crc16_modbus(nph + body))
        + struct.pack("<BIH", 2, unit_id, 0)
    )
    fields = {
        "cell": "G6CellNav00",
        "timestamp": epoch,
        "lon": round(lon, 7),
        "lat": round(lat, 7),
        "valid": True,
        "speed_kmh": speed,
        "course": course,
        "nsat": 12,
    }
    return (npl + nph + body).hex(), fields


def ingest_stats(state: S.VehicleState, n_units: int) -> S.IngestStats:
    ts = datetime.fromisoformat(state.last_seen)
    hex_frame, fields = sample_ndtp_frame(
        state.unit_id, ts, state.lon, state.lat, int(state.speed_kmh), int(state.heading)
    )
    return S.IngestStats(
        packets_total=18432,
        pps=round(n_units * SIM_SPEED / 12, 1),
        crc_errors=0,
        parse_errors=2,
        connections=n_units,
        reconnects=3,
        unknown_units=2,
        last_packet=S.LastPacket(
            received_at="2026-09-26T09:15:02Z", unit_id=state.unit_id, hex=hex_frame, fields=fields
        ),
    )
