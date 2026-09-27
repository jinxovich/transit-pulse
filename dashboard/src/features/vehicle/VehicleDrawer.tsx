import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ForecastPoint, Incident, VehicleDetail, VehicleState } from "@contract";
import { fetchVehicleDetail } from "../../api/vehicles";
import { fetchConfig } from "../../api/config";
import { useStream } from "../../store/stream";
import { useUi } from "../../store/ui";
import { formatDelay, formatDelayShort } from "../../lib/format";
import { formatSimTime, minutesBetween } from "../../lib/time";
import { RiskGlyph } from "../map/RiskGlyph";
import { DeviationChart } from "./DeviationChart";
import { StopTimeline } from "./StopTimeline";

const REFRESH_MS = 5_000;
const MAX_INCIDENT_LINKS = 4;
const STATUS_LABEL = { open: "новый", ack: "принят", resolved: "закрыт" } as const;

function Freshness({ v, simTime }: { v: VehicleState; simTime: string | null }) {
  if (!v.last_seen) return <span className="faint">нет данных</span>;
  if (v.stale && simTime) {
    const min = Math.max(0, Math.floor(minutesBetween(v.last_seen, simTime)));
    return <span className="tone-yellow">нет данных {min} мин</span>;
  }
  return <span>{formatSimTime(v.last_seen, true)}</span>;
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="vd-stat">
      <span className="vd-stat-label">{label}</span>
      <span className="vd-stat-value num">{children}</span>
    </div>
  );
}

function Header({ v, simTime }: { v: VehicleState; simTime: string | null }) {
  const p = v.prediction;
  const risk = v.kind === "scheduled" ? v.risk_level : "none";
  const title = v.kind === "unknown" ? "Неопознанный борт" : v.route_name ?? "Без маршрута";
  return (
    <header className="vd-head">
      <div className="vd-title">
        <RiskGlyph risk={risk} size={20} />
        <h2>{title}</h2>
        <span className="vd-id num">ТС {v.vehicle_id}</span>
        {p && (
          <span className={`chip ${p.model_mode === "ml" ? "chip-ml" : "chip-fallback"}`}>
            {p.model_mode === "ml" ? "ML-модель" : "упрощённый прогноз"}
          </span>
        )}
        {v.warming_up && <span className="chip">прогрев</span>}
      </div>
      <div className="vd-stats">
        <Stat label="Сейчас к графику">
          {v.current_dev_s != null ? formatDelay(v.current_dev_s) : "—"}
        </Stat>
        <Stat label={p ? `Прогноз на ${p.target_stop.planned_at ? formatSimTime(p.target_stop.planned_at) : "цель"}` : "Прогноз"}>
          {p ? (
            <>
              <b style={{ color: `var(--risk-${p.risk_level})` }}>{formatDelay(p.predicted_delay_s)}</b>
              <span className="vd-stat-aux"> ±{Math.round(p.expected_abs_error_s)} с</span>
            </>
          ) : (
            "—"
          )}
        </Stat>
        <Stat label="Вероятность опоздания">{p ? `${Math.round(p.p_late * 100)}%` : "—"}</Stat>
        <Stat label="Скорость">{v.speed_kmh != null ? `${Math.round(v.speed_kmh)} км/ч` : "—"}</Stat>
        <Stat label="Курс">
          {v.heading != null ? (
            <>
              <span className="vd-heading" style={{ transform: `rotate(${v.heading}deg)` }} aria-hidden>
                ↑
              </span>
              {Math.round(v.heading)}°
            </>
          ) : (
            "—"
          )}
        </Stat>
        <Stat label="Последние данные">
          <Freshness v={v} simTime={simTime} />
        </Stat>
      </div>
      {p && (
        <p className="vd-target small">
          <span className="dim">Цель прогноза:</span> {p.target_stop.name}
          {!p.horizon_ok && <span className="faint"> · вне окна 10–15 мин</span>}
          {v.next_stop && (
            <>
              <span className="dim"> · следующая:</span> {v.next_stop.name}
              {v.next_stop.planned_at && <span className="num"> в {formatSimTime(v.next_stop.planned_at)}</span>}
            </>
          )}
        </p>
      )}
    </header>
  );
}

function IncidentLinks({ ids }: { ids: string[] }) {
  const incidents = useStream((s) => s.incidents);
  const selectIncident = useUi((s) => s.selectIncident);
  const known = ids
    .map((id) => incidents[id])
    .filter((i): i is Incident => Boolean(i))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (known.length === 0) return null;
  const shown = known.slice(0, MAX_INCIDENT_LINKS);

  return (
    <div className="vd-incidents">
      <span className="dim small">Инциденты ТС</span>
      {shown.map((inc) => (
        <button key={inc.id} className={`vd-inc risk-${inc.risk_level}`} onClick={() => selectIncident(inc.id)}>
          <RiskGlyph risk={inc.status === "resolved" ? "none" : inc.risk_level} size={12} />
          <span className="num">{formatSimTime(inc.created_at)}</span>
          <span className="num">{formatDelay(inc.predicted_delay_s)}</span>
          <span className={`chip chip-${inc.status}`}>{STATUS_LABEL[inc.status]}</span>
        </button>
      ))}
      {known.length > shown.length && <span className="faint small">ещё {known.length - shown.length}</span>}
    </div>
  );
}

/** Точка прогноза: из потока (как в шапке и на карте), пока REST-ответ не обновился. */
function liveForecast(v: VehicleState, fallback: ForecastPoint | null): ForecastPoint | null {
  const p = v.prediction;
  if (!p || !p.target_stop.planned_at) return fallback;
  return { t: p.target_stop.planned_at, delay_s: p.predicted_delay_s, lo_s: p.interval_s[0], hi_s: p.interval_s[1] };
}

function Body({ detail, live, simTime }: { detail: VehicleDetail; live: VehicleState | undefined; simTime: string | null }) {
  const config = useQuery({ queryKey: ["config"], queryFn: fetchConfig, staleTime: Infinity });
  const thresholds = config.data?.thresholds;
  if (!thresholds) return null;
  const v = live ?? detail.vehicle;
  const forecast = liveForecast(v, detail.forecast);

  if (v.kind !== "scheduled") {
    return (
      <p className="vd-empty dim">
        {v.kind === "unknown"
          ? "Борт не из справочника: есть только позиция, расписания и прогноза нет."
          : "У ТС нет расписания на этот день: показываем только позицию."}
      </p>
    );
  }
  // Сим-время с точностью до минуты: график перестраивается раз в сим-минуту, а не на каждый тик.
  const minute = simTime ? `${simTime.slice(0, 16)}:00` : null;
  return (
    <div className="vd-body">
      <section className="vd-chart" aria-label="Отклонение от графика">
        <div className="vd-sec-head">
          <h3>Отклонение от графика</h3>
          <span className="vd-legend small">
            <span><i className="lg-line" /> факт за 60 мин</span>
            <span><i className="lg-dot" /> прогноз и интервал 80%</span>
            <span>
              <i className="lg-dash" /> пороги{" "}
              <span className="num">
                {[thresholds.early_delay_s, thresholds.yellow_delay_s, thresholds.red_delay_s].map(formatDelayShort).join(" · ")}
              </span>
            </span>
          </span>
        </div>
        <div className="vd-chart-box">
          {detail.deviation_series.length === 0 && !forecast ? (
            <p className="vd-empty dim">История отклонения пока не накоплена.</p>
          ) : (
            <DeviationChart
              series={detail.deviation_series}
              forecast={forecast}
              forecastRisk={v.prediction?.risk_level ?? "none"}
              thresholds={thresholds}
              simTime={minute}
            />
          )}
        </div>
      </section>
      <section className="vd-timeline" aria-label="Остановки">
        <div className="vd-sec-head">
          <h3>Остановки ±60 мин</h3>
        </div>
        <StopTimeline items={detail.timeline} thresholds={thresholds} simTime={simTime} />
      </section>
    </div>
  );
}

/** Drawer ТС: шапка, нитка отклонения с прогнозом и остановки. Данные — GET /vehicles/{id} раз в 5 с. */
export function VehicleDrawer({ id }: { id: string }) {
  const selectVehicle = useUi((s) => s.selectVehicle);
  const simTime = useStream((s) => s.simTime);
  const live = useStream((s) => s.vehicles[id]);
  const detail = useQuery({
    queryKey: ["vehicle", id],
    queryFn: () => fetchVehicleDetail(id),
    refetchInterval: REFRESH_MS,
  });
  // Шапка — из потока (обновляется каждую секунду), график и остановки — из REST.
  const vehicle = live ?? detail.data?.vehicle;

  return (
    <section className="vdrawer" aria-label="Транспортное средство">
      <button className="vd-close" onClick={() => selectVehicle(null)} aria-label="Закрыть (Esc)" title="Закрыть (Esc)">
        ×
      </button>
      {vehicle && <Header v={vehicle} simTime={simTime} />}
      {detail.data && <IncidentLinks ids={detail.data.incident_ids} />}
      {detail.isPending && <p className="vd-empty dim">Загружаем данные ТС…</p>}
      {detail.isError && !detail.data && (
        <div className="vd-empty">
          <p className="error">Не удалось загрузить: {detail.error.message}</p>
          <button className="whatif-btn" onClick={() => detail.refetch()}>
            Повторить
          </button>
        </div>
      )}
      {detail.data && <Body detail={detail.data} live={live} simTime={simTime} />}
    </section>
  );
}
