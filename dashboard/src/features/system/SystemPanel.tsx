import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Health, LatencyStats, SystemStatus } from "@contract";
import { fetchHealth, fetchIngestStats, fetchMetricsSummary, fetchQuality } from "../../api/system";
import { useStream } from "../../store/stream";
import { useUi } from "../../store/ui";
import { MODE_LABEL } from "../../lib/labels";
import { fmtAge, fmtCount, fmtMs, fmtPct, fmtValue } from "./fmt";
import { LeadHistogram } from "./LeadHistogram";
import { LastPacket } from "./LastPacket";

const FAST_MS = 5_000;
const SLOW_MS = 10_000;

const ML_STATUS: Record<SystemStatus["ml_status"], string> = {
  ok: "в норме",
  degraded: "с перебоями",
  down: "недоступен",
};

const CHECK_LABEL: Record<string, string> = {
  data: "Данные дня",
  ingest: "Приём NDTP",
  ml: "ML-сервис",
  stream: "Поток",
};

function Section({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`sys-sec ${className}`}>
      <header className="sys-sec-head">
        <h3>{title}</h3>
        {aside && <span className="dim small">{aside}</span>}
      </header>
      {children}
    </section>
  );
}

function Tile({ label, value, unit, tone, text }: { label: string; value: string; unit?: string; tone?: string; text?: boolean }) {
  return (
    <div className={`sys-tile${tone ? ` ${tone}` : ""}`}>
      <span className="sys-tile-label">{label}</span>
      <span className={`sys-tile-value${text ? "" : " num"}`}>
        {value}
        {unit && value !== "—" && <span className="sys-unit"> {unit}</span>}
      </span>
    </div>
  );
}

function Failed({ what, retry }: { what: string; retry: () => void }) {
  return (
    <p className="small">
      <span className="error">Не удалось загрузить {what}.</span>{" "}
      <button className="sys-retry" onClick={retry}>Повторить</button>
    </p>
  );
}

/** Проверка из /health: «ok: NDTP :9201» → зелёная точка и пояснение без «ok». */
function checkView(key: string, raw: string): { ok: boolean; text: string } {
  if (key === "stream") {
    const mode = raw as keyof typeof MODE_LABEL;
    return { ok: raw === "LIVE", text: MODE_LABEL[mode] ?? raw };
  }
  const ok = raw.startsWith("ok");
  const rest = raw.replace(/^ok:?\s*/, "");
  return { ok, text: ok ? rest || "в норме" : raw };
}

function ModeSection({ health }: { health: Health | undefined }) {
  const status = useStream((s) => s.status);
  const connected = useStream((s) => s.connected);
  const mode = connected ? status?.mode ?? "WARMING_UP" : "OFFLINE";
  const checks = Object.entries(health?.checks ?? {}).filter(([k]) => k in CHECK_LABEL);
  const features = /\((\d+)\)/.exec(health?.checks.pipeline ?? "")?.[1];

  return (
    <Section title="Режим потока и ML" aside={health ? `версия ${health.version}` : undefined} className="sys-mode">
      <div className="sys-mode-row">
        <div className={`badge badge-${mode.toLowerCase()}`}>
          <span className="badge-dot" />
          {connected ? MODE_LABEL[mode] : "Нет связи"}
        </div>
        <Tile text label="ML-сервис" value={status ? ML_STATUS[status.ml_status] : "—"} tone={status && status.ml_status !== "ok" ? "tone-yellow" : undefined} />
        <Tile
          text
          label="Прогноз"
          value={status ? (status.model_mode === "ml" ? "ML-модель" : "эвристика") : "—"}
          tone={status?.model_mode === "fallback" ? "tone-yellow" : undefined}
        />
        <Tile label="Версия модели" value={status?.model_version || "—"} />
        <Tile label="Бортов на связи" value={fmtValue(status?.units_connected)} />
        <Tile label="Последний пакет" value={fmtAge(status?.last_packet_age_s)} unit="с назад" />
      </div>
      {checks.length > 0 && (
        <ul className="sys-checks">
          {checks.map(([k, raw]) => {
            const c = checkView(k, raw);
            return (
              <li key={k}>
                <span className={`sys-dot ${c.ok ? "is-ok" : "is-bad"}`} aria-hidden />
                <span>{CHECK_LABEL[k]}</span>
                <span className="dim">{c.text}</span>
              </li>
            );
          })}
          {features && (
            <li>
              <span className="sys-dot is-ok" aria-hidden />
              <span>Конвейер</span>
              <span className="dim">{features} признаков</span>
            </li>
          )}
        </ul>
      )}
    </Section>
  );
}

const STAGES: { key: "ingest_to_state" | "pass_to_ws" | "ml_batch"; label: string; hint: string }[] = [
  { key: "ingest_to_state", label: "Приём → состояние", hint: "пакет NDTP разобран и учтён" },
  { key: "pass_to_ws", label: "Прогноз → WS", hint: "проход прогноза до рассылки" },
  { key: "ml_batch", label: "Батч ML", hint: "инференс модели по всем ТС" },
];

function PerfSection() {
  const q = useQuery({ queryKey: ["metrics-summary"], queryFn: fetchMetricsSummary, refetchInterval: FAST_MS });
  const m = q.data;
  return (
    <Section title="Производительность" aside="обновление раз в 5 с">
      {q.isError && !m && <Failed what="метрики" retry={() => q.refetch()} />}
      <div className="sys-tiles">
        <Tile label="Пакетов/с" value={fmtValue(m?.kpis.ingest_pps, 1)} />
        <Tile label="Сквозная p95" value={fmtMs(m?.kpis.e2e_latency_ms_p95)} unit="мс" />
        <Tile label="Лаг очереди" value={fmtCount(m?.queue_lag)} tone={m?.queue_lag ? "tone-yellow" : undefined} />
        <Tile label="Потеряно" value={fmtCount(m?.dropped_packets)} tone={m?.dropped_packets ? "tone-red" : undefined} />
      </div>
      <table className="sys-table">
        <thead>
          <tr>
            <th>Этап, мс</th>
            <th className="r">p50</th>
            <th className="r">p95</th>
            <th className="r">max</th>
          </tr>
        </thead>
        <tbody>
          {STAGES.map((s) => {
            const l: LatencyStats | undefined = m?.[s.key];
            return (
              <tr key={s.key}>
                <td>
                  <div>{s.label}</div>
                  <div className="faint small">{s.hint}</div>
                </td>
                <td className="r num">{fmtMs(l?.p50_ms)}</td>
                <td className="r num sys-strong">{fmtMs(l?.p95_ms)}</td>
                <td className="r num dim">{fmtMs(l?.max_ms)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Section>
  );
}

function QualitySection() {
  const q = useQuery({ queryKey: ["metrics-quality"], queryFn: fetchQuality, refetchInterval: SLOW_MS });
  const m = q.data;
  const alerts = m?.lead_hist.reduce((sum, b) => sum + b.count, 0) ?? 0;
  const off = m?.offline;

  return (
    <Section title="Качество на потоке" aside="сверка с фактом по GPS">
      {q.isError && !m && <Failed what="метрики качества" retry={() => q.refetch()} />}
      <div className="sys-lead">
        <div className="sys-lead-main">
          <span className="sys-lead-num num">{fmtPct(m?.lead_ok_share)}</span>
          <span className="sys-lead-cap">
            алертов созданы за <b>≥ 10 мин</b> до события
            {alerts > 0 && <span className="dim num"> · {fmtCount(alerts)} алертов</span>}
          </span>
        </div>
        <div className="sys-lead-hist">
          <span className="faint small">упреждение, мин</span>
          {m && m.lead_hist.length > 0 ? <LeadHistogram buckets={m.lead_hist} /> : <p className="faint small">—</p>}
        </div>
      </div>
      <div className="sys-tiles">
        <Tile label="Онлайн-MAE" value={fmtValue(m?.online_mae_s)} unit="с" />
        <Tile label="Сверено" value={fmtValue(m?.n_resolved)} />
        <Tile label="Точность" value={fmtPct(m?.alert_precision)} />
        <Tile label="Полнота" value={fmtPct(m?.alert_recall)} />
      </div>
      {off && off.cv_mae_baseline_s > 0 && (
        <div className="sys-offline">
          <span className="dim small">Офлайн-валидация (CV), MAE</span>
          <div className="sys-offline-row num">
            <span className="dim">базовый прогноз {fmtValue(off.cv_mae_baseline_s)} с</span>
            <span className="whatif-arrow">→</span>
            <b>модель {fmtValue(off.cv_mae_model_s)} с</b>
            <span className="sys-gain">−{Math.round(off.improvement * 100)}% ошибки</span>
          </div>
          <div className="sys-offline-bars" aria-hidden>
            <i style={{ width: "100%" }} />
            <i className="is-model" style={{ width: `${(off.cv_mae_model_s / off.cv_mae_baseline_s) * 100}%` }} />
          </div>
        </div>
      )}
    </Section>
  );
}

function IngestSection() {
  const q = useQuery({ queryKey: ["ingest-stats"], queryFn: fetchIngestStats, refetchInterval: FAST_MS });
  const s = q.data;
  return (
    <Section title="Приём NDTP" aside={s ? `${fmtCount(s.packets_total)} пакетов всего` : undefined} className="sys-ingest">
      {q.isError && !s && <Failed what="статистику приёма" retry={() => q.refetch()} />}
      <div className="sys-tiles sys-tiles-6">
        <Tile label="Пакетов/с" value={fmtValue(s?.pps, 1)} />
        <Tile label="Соединений" value={fmtCount(s?.connections)} />
        <Tile label="Реконнектов" value={fmtCount(s?.reconnects)} />
        <Tile label="Ошибок CRC" value={fmtCount(s?.crc_errors)} tone={s?.crc_errors ? "tone-red" : undefined} />
        <Tile label="Ошибок разбора" value={fmtCount(s?.parse_errors)} tone={s?.parse_errors ? "tone-yellow" : undefined} />
        <Tile label="Неопознанных" value={fmtCount(s?.unknown_units)} />
      </div>
      {s?.last_packet ? <LastPacket packet={s.last_packet} /> : s && <p className="faint small">Пакетов ещё не было.</p>}
    </Section>
  );
}

function Links() {
  const ml = `${location.protocol}//${location.hostname}:8001/docs`;
  const links = [
    { href: "/docs", label: "Swagger бэкенда", hint: "REST и WS-контракт" },
    { href: ml, label: "Swagger ML", hint: "сервис прогноза :8001" },
    { href: "/code-docs/", label: "Документация кода", hint: "Sphinx" },
    { href: "/metrics", label: "Метрики Prometheus", hint: "/metrics" },
  ];
  return (
    <nav className="sys-links" aria-label="Документация и метрики">
      {links.map((l) => (
        <a key={l.href} href={l.href} target="_blank" rel="noopener noreferrer">
          <span>{l.label} ↗</span>
          <span className="faint small">{l.hint}</span>
        </a>
      ))}
    </nav>
  );
}

/** Панель «Система»: режим, производительность, качество на потоке и приём NDTP — доказательства для жюри. */
export function SystemPanel() {
  const close = () => useUi.getState().setSystemOpen(false);
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: SLOW_MS });

  return (
    <>
      <div className="sys-backdrop" onClick={close} aria-hidden />
      <aside className="sys-panel" aria-label="Система">
        <header className="sys-head">
          <h2>Система</h2>
          <span className="dim small">производительность и надёжность в реальном времени</span>
          <button className="vd-close" onClick={close} aria-label="Закрыть (Esc)" title="Закрыть (Esc)">
            ×
          </button>
        </header>
        <div className="sys-body">
          <ModeSection health={health.data} />
          <div className="sys-cols">
            <QualitySection />
            <PerfSection />
          </div>
          <IngestSection />
          <Links />
        </div>
      </aside>
    </>
  );
}
