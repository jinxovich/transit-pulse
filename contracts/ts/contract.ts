// АВТОГЕНЕРАЦИЯ из packages/transit_core/transit_core/schemas.py — не редактировать руками.
// Обновление: uv run python -m transit_core.contract_export --out contracts

export const SCHEMA_VERSION = "1.0";

/** Время датасета без часового пояса: YYYY-MM-DDTHH:MM:SS. Показывать как есть. */
export type NaiveTime = string;
export type RiskLevel = "green" | "yellow" | "red" | "early" | "none";
export type VehicleKind = "scheduled" | "no_schedule" | "unknown";
export type StreamMode = "LIVE" | "WARMING_UP" | "DEGRADED" | "OFFLINE" | "PAUSED";
export type CauseCode = "ACCUMULATED_DELAY" | "CONGESTION" | "LONG_DWELL" | "SHORT_LAYOVER" | "EARLY_RUNNING" | "GPS_LOSS" | "UNKNOWN";
export type IncidentStatus = "open" | "ack" | "resolved";
export type IncidentOutcome = "pending" | "hit" | "false_alarm" | "miss";
export type ModelMode = "ml" | "fallback";

/** Реакция диспетчера на инцидент. */
export interface AckInfo {
  action_code: string | null;
  comment: string | null;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  at: NaiveTime;
}

/** Тело `POST /api/v1/incidents/{id}/ack`. */
export interface AckRequest {
  action_code: string | null;
  comment: string | null;
}

/** Ответ `GET /api/v1/config`. */
export interface AppConfig {
  schema_version: string;
  thresholds: Thresholds;
  colors: RiskColors;
  horizon_min: [number, number];
  dataset_day: string;
  sim_speed: number;
  session_id: string;
}

/** Предполагаемая причина задержки. */
export interface Cause {
  code: CauseCode;
  title: string;
  details: string;
  evidence: Evidence[];
}

export interface DeviationPoint {
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  t: NaiveTime;
  dev_s: number;
}

/** Факт, объясняющий прогноз (вклад признака). */
export interface Evidence {
  feature: string;
  /** Человекочитаемое название признака */
  label: string;
  /** Отформатированное значение, например '4.2 км/ч' */
  value: string;
  /** Вклад в прогноз, c */
  contribution_s: number | null;
}

export interface ForecastPoint {
  /** Плановое время целевой остановки */
  t: NaiveTime;
  delay_s: number;
  lo_s: number;
  hi_s: number;
}

export interface Health {
  status: "ok" | "degraded" | "error";
  version: string;
  checks: Record<string, string>;
}

/** Карточка инцидента: ТС с высоким риском задержки. */
export interface Incident {
  id: string;
  vehicle_id: string;
  tr_id: string | null;
  route_name: string | null;
  status: IncidentStatus;
  risk_level: RiskLevel;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  created_at: NaiveTime;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  updated_at: NaiveTime;
  /** За сколько минут до события создан алерт */
  lead_min: number;
  target_stop: StopRef;
  predicted_delay_s: number;
  p_late: number;
  interval_s: [number, number];
  expected_abs_error_s: number;
  cause: Cause;
  segment: Segment | null;
  recommendations: Recommendation[];
  ack: AckInfo | null;
  /** Фактическая задержка (по GPS) после события */
  actual_delay_s: number | null;
  outcome: IncidentOutcome;
}

/** Ответ `GET /api/v1/ingest/stats`: приём NDTP. */
export interface IngestStats {
  packets_total: number;
  pps: number;
  crc_errors: number;
  parse_errors: number;
  connections: number;
  reconnects: number;
  unknown_units: number;
  last_packet: LastPacket | null;
}

/** Сводка для верхней полосы дашборда. */
export interface Kpis {
  vehicles_online: number;
  vehicles_total: number;
  by_risk: RiskCounts;
  incidents_open: number;
  avg_predicted_delay_s: number | null;
  /** Доля алертов с упреждением ≥ 10 мин */
  lead_ok_share: number | null;
  e2e_latency_ms_p95: number | null;
  ml_latency_ms_p95: number | null;
  ingest_pps: number;
}

export interface LastPacket {
  /** Wall-clock время приёма, ISO UTC */
  received_at: string;
  unit_id: number;
  /** Сырой NDTP-кадр в hex */
  hex: string;
  fields: Record<string, number | number | boolean | string>;
}

export interface LatencyStats {
  p50_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
}

export interface LeadBucket {
  lead_min: number;
  count: number;
}

/** GeoJSON LineString, координаты `[lon, lat]`. */
export interface LineString {
  type: "LineString";
  coordinates: [number, number][];
}

/** Ответ `GET /api/v1/metrics/summary`: производительность. */
export interface MetricsSummary {
  kpis: Kpis;
  ingest_to_state: LatencyStats;
  pass_to_ws: LatencyStats;
  ml_batch: LatencyStats;
  queue_lag: number;
  dropped_packets: number;
}

/** Статическая маршрутная сеть дня: ответ `GET /api/v1/network`. */
export interface Network {
  stops: Stop[];
  routes: Route[];
  segments: NetworkSegment[];
  /** [minLon, minLat, maxLon, maxLat] */
  bbox: [number, number, number, number];
}

/** Перегон между соседними остановками маршрута. */
export interface NetworkSegment {
  segment_id: string;
  route_id: string;
  from_stop_key: string;
  to_stop_key: string;
  geometry: LineString;
}

export interface OfflineMetrics {
  cv_mae_baseline_s: number;
  cv_mae_model_s: number;
  /** Доля снижения MAE к baseline */
  improvement: number;
}

/** Прогноз задержки на целевой остановке в горизонте 10–15 минут. */
export interface Prediction {
  target_stop: StopRef;
  /** Прогноз задержки, c (+ опоздание) */
  predicted_delay_s: number;
  /** Вероятность опоздания > 120 c */
  p_late: number;
  /** Интервал прогноза [q10, q90], c */
  interval_s: [number, number];
  /** Ожидаемая абсолютная ошибка, c */
  expected_abs_error_s: number;
  /** Минут от generated_at до planned_at цели */
  lead_min: number;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  generated_at: NaiveTime;
  /** Цель в окне (T+10, T+15]; false — перерыв/конечная */
  horizon_ok: boolean;
  model_version: string;
  model_mode: ModelMode;
  risk_level: RiskLevel;
  cause_code: CauseCode;
}

/** Ответ `GET /api/v1/metrics/quality`: качество прогнозов на потоке. */
export interface QualityMetrics {
  online_mae_s: number | null;
  n_resolved: number;
  lead_ok_share: number | null;
  lead_hist: LeadBucket[];
  alert_precision: number | null;
  alert_recall: number | null;
  offline: OfflineMetrics;
}

/** Упреждающее действие для диспетчера. */
export interface Recommendation {
  code: string;
  title: string;
  description: string;
}

/** Тело `POST /api/v1/replay/control`. */
export interface ReplayControl {
  action: "start" | "pause" | "resume" | "speed" | "seek";
  speed: number | null;
  seek_to: NaiveTime | null;
}

export interface RiskColors {
  green: string;
  yellow: string;
  red: string;
  early: string;
  none: string;
  stale: string;
}

export interface RiskCounts {
  green: number;
  yellow: number;
  red: number;
  early: number;
  none: number;
}

/** Маршрут одного ТС за день (объединение его рейсов). */
export interface Route {
  route_id: string;
  /** «Конечная A ↔ Конечная B» */
  name: string;
  /** Цвет линии маршрута в легенде, hex */
  color: string;
  vehicle_ids: string[];
  stop_keys: string[];
}

/** Участок маршрута, на котором ожидается сбой. */
export interface Segment {
  segment_id: string | null;
  from_stop: StopRef;
  to_stop: StopRef;
  geometry: LineString;
}

/** Риск на перегоне (для окраски участков на карте). */
export interface SegmentRisk {
  segment_id: string;
  route_id: string;
  risk_level: RiskLevel;
  vehicle_ids: string[];
  /** Текущая скорость / типичная для перегона */
  speed_ratio: number | null;
}

export interface SimClock {
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  sim_time: NaiveTime;
  session_id: string;
  speed: number;
  state: "running" | "paused" | "stopped";
}

export interface SnapshotData {
  vehicles: VehicleState[];
  incidents: Incident[];
  kpis: Kpis;
  status: SystemStatus;
}

/** Остановка (физическая точка), общая для всех плановых прибытий. */
export interface Stop {
  /** Стабильный ключ остановки */
  stop_key: string;
  /** Адрес остановки из расписания */
  name: string;
  lon: number;
  lat: number;
}

/** Ссылка на плановое прибытие ТС на остановку. */
export interface StopRef {
  /** ID планового прибытия (tt_action_item_id) */
  visit_id: string | null;
  stop_key: string;
  name: string;
  lon: number;
  lat: number;
  planned_at: NaiveTime | null;
}

/** Строка «нитки графика» ТС: план, виртуальный факт по GPS и прогноз. */
export interface StopTimelineItem {
  visit_id: string;
  stop_key: string;
  name: string;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  planned_at: NaiveTime;
  /** Прибытие по GPS (для пройденных) */
  actual_at: NaiveTime | null;
  actual_delay_s: number | null;
  predicted_delay_s: number | null;
  is_target: boolean;
  status: "passed" | "upcoming";
}

/** Режим работы системы (баннеры LIVE/DEGRADED/...). */
export interface SystemStatus {
  mode: StreamMode;
  /** Пояснение для баннера */
  reason: string | null;
  /** Секунд (wall-clock) с последнего пакета */
  last_packet_age_s: number | null;
  units_connected: number;
  ml_status: "ok" | "degraded" | "down";
  model_version: string;
  model_mode: ModelMode;
}

/** Пороги риска; фронт берёт их только отсюда, не хардкодит. */
export interface Thresholds {
  red_delay_s: number;
  yellow_delay_s: number;
  early_delay_s: number;
  red_p_late: number;
  yellow_p_late: number;
}

/** Ответ `GET /api/v1/vehicles/{vehicle_id}`. */
export interface VehicleDetail {
  vehicle: VehicleState;
  /** Остановки в окне ±60 мин */
  timeline: StopTimelineItem[];
  /** Отклонение за последние 60 мин */
  deviation_series: DeviationPoint[];
  forecast: ForecastPoint | null;
  incident_ids: string[];
}

/** Текущее состояние ТС на карте. */
export interface VehicleState {
  /** tr_id или 'u:<unit_id>' для неопознанного борта */
  vehicle_id: string;
  kind: VehicleKind;
  tr_id: string | null;
  unit_id: number;
  route_id: string | null;
  route_name: string | null;
  /** null, пока нет ни одной валидной координаты */
  lon: number | null;
  lat: number | null;
  /** Курс, градусы 0–360 */
  heading: number | null;
  speed_kmh: number | null;
  last_seen: NaiveTime | null;
  /** Давно нет данных от ТС */
  stale: boolean;
  /** Мало истории для прогноза, инциденты не создаются */
  warming_up: boolean;
  /** Отклонение от графика сейчас, c */
  current_dev_s: number | null;
  next_stop: StopRef | null;
  prediction: Prediction | null;
  risk_level: RiskLevel;
  open_incident_id: string | null;
}

export interface VehiclesDeltaData {
  /** Изменившиеся ТС целиком */
  vehicles: VehicleState[];
  /** vehicle_id, которые надо убрать с карты */
  removed: string[];
}

export interface WsIncident {
  schema_version: string;
  /** Меняется при перезапуске/перемотке потока */
  session_id: string;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  sim_time: NaiveTime;
  type: "incident.opened" | "incident.updated" | "incident.resolved";
  data: Incident;
}

export interface WsKpis {
  schema_version: string;
  /** Меняется при перезапуске/перемотке потока */
  session_id: string;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  sim_time: NaiveTime;
  type: "kpis";
  data: Kpis;
}

/** Любое сообщение /ws/v1/stream; различается по полю type. */
export type WsMessage = WsSnapshot | WsVehiclesDelta | WsIncident | WsKpis | WsSystemStatus | WsPing;

export interface WsPing {
  schema_version: string;
  /** Меняется при перезапуске/перемотке потока */
  session_id: string;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  sim_time: NaiveTime;
  type: "ping";
  data: Record<string, string>;
}

/** Полное состояние: при подключении и при смене session_id. */
export interface WsSnapshot {
  schema_version: string;
  /** Меняется при перезапуске/перемотке потока */
  session_id: string;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  sim_time: NaiveTime;
  type: "snapshot";
  data: SnapshotData;
}

export interface WsSystemStatus {
  schema_version: string;
  /** Меняется при перезапуске/перемотке потока */
  session_id: string;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  sim_time: NaiveTime;
  type: "system.status";
  data: SystemStatus;
}

export interface WsVehiclesDelta {
  schema_version: string;
  /** Меняется при перезапуске/перемотке потока */
  session_id: string;
  /** Время датасета без часового пояса, например 2026-01-06T07:14:00 */
  sim_time: NaiveTime;
  type: "vehicles.delta";
  data: VehiclesDeltaData;
}
