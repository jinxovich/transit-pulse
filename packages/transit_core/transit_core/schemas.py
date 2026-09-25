"""Контракт REST API и WebSocket между backend и дашбордом диспетчера.

Это единственный источник правды для фронтенда: из этих моделей генерируются
OpenAPI (FastAPI), JSON Schema для WS-сообщений и TypeScript-типы
(``contracts/ts/contract.ts``).

Соглашения:

* **Время** — naive ISO-строки ``YYYY-MM-DDTHH:MM:SS`` во «времени датасета»
  (как в CSV организаторов). Фронт показывает их как есть, без сдвига часового пояса.
* **Задержка** в секундах: ``+`` — опоздание, ``−`` — опережение графика.
* **lead_min** — сколько сим-минут от момента прогноза (``generated_at``)
  до планового прибытия на целевую остановку.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"

NaiveTime = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",
        description="Время датасета без часового пояса, например 2026-01-06T07:14:00",
        examples=["2026-01-06T07:14:00"],
    ),
]

RiskLevel = Literal["green", "yellow", "red", "early", "none"]
"""Уровень риска: ``early`` — опережение графика, ``none`` — прогноза нет."""
VehicleKind = Literal["scheduled", "no_schedule", "unknown"]
"""``scheduled`` — есть расписание и прогнозы; ``no_schedule`` — ТС из датасета без
расписания; ``unknown`` — борт, которого нет в справочнике (например, из эмулятора)."""
StreamMode = Literal["LIVE", "WARMING_UP", "DEGRADED", "OFFLINE", "PAUSED"]
CauseCode = Literal[
    "ACCUMULATED_DELAY",
    "CONGESTION",
    "LONG_DWELL",
    "SHORT_LAYOVER",
    "EARLY_RUNNING",
    "GPS_LOSS",
    "UNKNOWN",
]
IncidentStatus = Literal["open", "ack", "resolved"]
IncidentOutcome = Literal["pending", "hit", "false_alarm", "miss"]
ModelMode = Literal["ml", "fallback"]


class Contract(BaseModel):
    """База контракта: неизменяемые объекты и запрет лишних полей."""

    # Поля с дефолтами в ответах есть всегда — в TS-типах они обязательные.
    model_config = ConfigDict(
        frozen=True, extra="forbid", json_schema_serialization_defaults_required=True
    )


class LineString(Contract):
    """GeoJSON LineString, координаты ``[lon, lat]``."""

    type: Literal["LineString"] = "LineString"
    coordinates: list[tuple[float, float]]


# --------------------------------------------------------------------------- сеть


class Stop(Contract):
    """Остановка (физическая точка), общая для всех плановых прибытий."""

    stop_key: str = Field(description="Стабильный ключ остановки")
    name: str = Field(description="Адрес остановки из расписания")
    lon: float
    lat: float


class Route(Contract):
    """Маршрут одного ТС за день (объединение его рейсов)."""

    route_id: str
    name: str = Field(description="«Конечная A ↔ Конечная B»")
    color: str = Field(description="Цвет линии маршрута в легенде, hex")
    vehicle_ids: list[str]
    stop_keys: list[str]


class NetworkSegment(Contract):
    """Перегон между соседними остановками маршрута."""

    segment_id: str
    route_id: str
    from_stop_key: str
    to_stop_key: str
    geometry: LineString


class Network(Contract):
    """Статическая маршрутная сеть дня: ответ ``GET /api/v1/network``."""

    stops: list[Stop]
    routes: list[Route]
    segments: list[NetworkSegment]
    bbox: tuple[float, float, float, float] = Field(description="[minLon, minLat, maxLon, maxLat]")


# ---------------------------------------------------------------------- прогнозы


class StopRef(Contract):
    """Ссылка на плановое прибытие ТС на остановку."""

    visit_id: str | None = Field(description="ID планового прибытия (tt_action_item_id)")
    stop_key: str
    name: str
    lon: float
    lat: float
    planned_at: NaiveTime | None


class Prediction(Contract):
    """Прогноз задержки на целевой остановке в горизонте 10–15 минут."""

    target_stop: StopRef
    predicted_delay_s: float = Field(description="Прогноз задержки, c (+ опоздание)")
    p_late: float = Field(ge=0, le=1, description="Вероятность опоздания > 120 c")
    interval_s: tuple[float, float] = Field(description="Интервал прогноза [q10, q90], c")
    expected_abs_error_s: float = Field(description="Ожидаемая абсолютная ошибка, c")
    lead_min: float = Field(description="Минут от generated_at до planned_at цели")
    generated_at: NaiveTime
    horizon_ok: bool = Field(description="Цель в окне (T+10, T+15]; false — перерыв/конечная")
    model_version: str
    model_mode: ModelMode
    risk_level: RiskLevel
    cause_code: CauseCode


class VehicleState(Contract):
    """Текущее состояние ТС на карте."""

    vehicle_id: str = Field(description="tr_id или 'u:<unit_id>' для неопознанного борта")
    kind: VehicleKind
    tr_id: str | None
    unit_id: int
    route_id: str | None
    route_name: str | None
    lon: float | None = Field(description="null, пока нет ни одной валидной координаты")
    lat: float | None
    heading: float | None = Field(description="Курс, градусы 0–360")
    speed_kmh: float | None
    last_seen: NaiveTime | None
    stale: bool = Field(description="Давно нет данных от ТС")
    warming_up: bool = Field(description="Мало истории для прогноза, инциденты не создаются")
    current_dev_s: float | None = Field(description="Отклонение от графика сейчас, c")
    next_stop: StopRef | None
    prediction: Prediction | None
    risk_level: RiskLevel
    open_incident_id: str | None


# ---------------------------------------------------------------------- инциденты


class Evidence(Contract):
    """Факт, объясняющий прогноз (вклад признака)."""

    feature: str
    label: str = Field(description="Человекочитаемое название признака")
    value: str = Field(description="Отформатированное значение, например '4.2 км/ч'")
    contribution_s: float | None = Field(description="Вклад в прогноз, c")


class Cause(Contract):
    """Предполагаемая причина задержки."""

    code: CauseCode
    title: str
    details: str
    evidence: list[Evidence]


class Recommendation(Contract):
    """Упреждающее действие для диспетчера."""

    code: str
    title: str
    description: str


class Segment(Contract):
    """Участок маршрута, на котором ожидается сбой."""

    segment_id: str | None
    from_stop: StopRef
    to_stop: StopRef
    geometry: LineString


class AckInfo(Contract):
    """Реакция диспетчера на инцидент."""

    action_code: str | None
    comment: str | None
    at: NaiveTime


class Incident(Contract):
    """Карточка инцидента: ТС с высоким риском задержки."""

    id: str
    vehicle_id: str
    tr_id: str | None
    route_name: str | None
    status: IncidentStatus
    risk_level: RiskLevel
    created_at: NaiveTime
    updated_at: NaiveTime
    lead_min: float = Field(description="За сколько минут до события создан алерт")
    target_stop: StopRef
    predicted_delay_s: float
    p_late: float = Field(ge=0, le=1)
    interval_s: tuple[float, float]
    expected_abs_error_s: float
    cause: Cause
    segment: Segment | None
    recommendations: list[Recommendation]
    ack: AckInfo | None
    actual_delay_s: float | None = Field(description="Фактическая задержка (по GPS) после события")
    outcome: IncidentOutcome


# ------------------------------------------------------------------ KPI и статус


class RiskCounts(Contract):
    green: int
    yellow: int
    red: int
    early: int
    none: int


class Kpis(Contract):
    """Сводка для верхней полосы дашборда."""

    vehicles_online: int
    vehicles_total: int
    by_risk: RiskCounts
    incidents_open: int
    avg_predicted_delay_s: float | None
    lead_ok_share: float | None = Field(description="Доля алертов с упреждением ≥ 10 мин")
    e2e_latency_ms_p95: float | None
    ml_latency_ms_p95: float | None
    ingest_pps: float


class SystemStatus(Contract):
    """Режим работы системы (баннеры LIVE/DEGRADED/...)."""

    mode: StreamMode
    reason: str | None = Field(description="Пояснение для баннера")
    last_packet_age_s: float | None = Field(description="Секунд (wall-clock) с последнего пакета")
    units_connected: int
    ml_status: Literal["ok", "degraded", "down"]
    model_version: str
    model_mode: ModelMode


class Thresholds(Contract):
    """Пороги риска; фронт берёт их только отсюда, не хардкодит."""

    red_delay_s: float = 120
    yellow_delay_s: float = 60
    early_delay_s: float = -60
    red_p_late: float = 0.5
    yellow_p_late: float = 0.3


class RiskColors(Contract):
    green: str = "#2FBF71"
    yellow: str = "#F2B134"
    red: str = "#E5484D"
    early: str = "#3E9BFF"
    none: str = "#8B93A1"
    stale: str = "#6B7280"


class AppConfig(Contract):
    """Ответ ``GET /api/v1/config``."""

    schema_version: str = SCHEMA_VERSION
    thresholds: Thresholds = Thresholds()
    colors: RiskColors = RiskColors()
    horizon_min: tuple[int, int] = (10, 15)
    dataset_day: str = "2026-01-06"
    sim_speed: float
    session_id: str


# --------------------------------------------------------------- детали ТС и метрики


class StopTimelineItem(Contract):
    """Строка «нитки графика» ТС: план, виртуальный факт по GPS и прогноз."""

    visit_id: str
    stop_key: str
    name: str
    planned_at: NaiveTime
    actual_at: NaiveTime | None = Field(description="Прибытие по GPS (для пройденных)")
    actual_delay_s: float | None
    predicted_delay_s: float | None
    is_target: bool
    status: Literal["passed", "upcoming"]


class DeviationPoint(Contract):
    t: NaiveTime
    dev_s: float


class ForecastPoint(Contract):
    t: NaiveTime = Field(description="Плановое время целевой остановки")
    delay_s: float
    lo_s: float
    hi_s: float


class VehicleDetail(Contract):
    """Ответ ``GET /api/v1/vehicles/{vehicle_id}``."""

    vehicle: VehicleState
    timeline: list[StopTimelineItem] = Field(description="Остановки в окне ±60 мин")
    deviation_series: list[DeviationPoint] = Field(description="Отклонение за последние 60 мин")
    forecast: ForecastPoint | None
    incident_ids: list[str]


class SegmentRisk(Contract):
    """Риск на перегоне (для окраски участков на карте)."""

    segment_id: str
    route_id: str
    risk_level: RiskLevel
    vehicle_ids: list[str]
    speed_ratio: float | None = Field(description="Текущая скорость / типичная для перегона")


class LatencyStats(Contract):
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None


class MetricsSummary(Contract):
    """Ответ ``GET /api/v1/metrics/summary``: производительность."""

    kpis: Kpis
    ingest_to_state: LatencyStats
    pass_to_ws: LatencyStats
    ml_batch: LatencyStats
    queue_lag: int
    dropped_packets: int


class LeadBucket(Contract):
    lead_min: int
    count: int


class OfflineMetrics(Contract):
    cv_mae_baseline_s: float
    cv_mae_model_s: float
    improvement: float = Field(description="Доля снижения MAE к baseline")


class QualityMetrics(Contract):
    """Ответ ``GET /api/v1/metrics/quality``: качество прогнозов на потоке."""

    online_mae_s: float | None
    n_resolved: int
    lead_ok_share: float | None
    lead_hist: list[LeadBucket]
    alert_precision: float | None
    alert_recall: float | None
    offline: OfflineMetrics


class LastPacket(Contract):
    received_at: str = Field(description="Wall-clock время приёма, ISO UTC")
    unit_id: int
    hex: str = Field(description="Сырой NDTP-кадр в hex")
    fields: dict[str, float | int | bool | str]


class IngestStats(Contract):
    """Ответ ``GET /api/v1/ingest/stats``: приём NDTP."""

    packets_total: int
    pps: float
    crc_errors: int
    parse_errors: int
    connections: int
    reconnects: int
    unknown_units: int
    last_packet: LastPacket | None


class SimClock(Contract):
    sim_time: NaiveTime
    session_id: str
    speed: float
    state: Literal["running", "paused", "stopped"]


class Health(Contract):
    status: Literal["ok", "degraded", "error"]
    version: str
    checks: dict[str, str]


class AckRequest(Contract):
    """Тело ``POST /api/v1/incidents/{id}/ack``."""

    action_code: str | None = None
    comment: str | None = None


class ReplayControl(Contract):
    """Тело ``POST /api/v1/replay/control``."""

    action: Literal["start", "pause", "resume", "speed", "seek"]
    speed: float | None = None
    seek_to: NaiveTime | None = None


# --------------------------------------------------------------------- WebSocket


class WsBase(Contract):
    schema_version: str = SCHEMA_VERSION
    session_id: str = Field(description="Меняется при перезапуске/перемотке потока")
    sim_time: NaiveTime


class SnapshotData(Contract):
    vehicles: list[VehicleState]
    incidents: list[Incident]
    kpis: Kpis
    status: SystemStatus


class VehiclesDeltaData(Contract):
    vehicles: list[VehicleState] = Field(description="Изменившиеся ТС целиком")
    removed: list[str] = Field(description="vehicle_id, которые надо убрать с карты")


class WsSnapshot(WsBase):
    """Полное состояние: при подключении и при смене session_id."""

    type: Literal["snapshot"] = "snapshot"
    data: SnapshotData


class WsVehiclesDelta(WsBase):
    type: Literal["vehicles.delta"] = "vehicles.delta"
    data: VehiclesDeltaData


class WsIncident(WsBase):
    type: Literal["incident.opened", "incident.updated", "incident.resolved"]
    data: Incident


class WsKpis(WsBase):
    type: Literal["kpis"] = "kpis"
    data: Kpis


class WsSystemStatus(WsBase):
    type: Literal["system.status"] = "system.status"
    data: SystemStatus


class WsPing(WsBase):
    type: Literal["ping"] = "ping"
    data: dict[str, str] = Field(default_factory=dict)


WsMessage = Annotated[
    WsSnapshot | WsVehiclesDelta | WsIncident | WsKpis | WsSystemStatus | WsPing,
    Field(discriminator="type"),
]
"""Любое сообщение ``/ws/v1/stream``; тип различается по полю ``type``."""

REST_MODELS: tuple[type[BaseModel], ...] = (
    Health,
    AppConfig,
    Network,
    VehicleState,
    VehicleDetail,
    Incident,
    SegmentRisk,
    MetricsSummary,
    QualityMetrics,
    IngestStats,
    SimClock,
    AckRequest,
    ReplayControl,
)
