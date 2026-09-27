import { useMutation } from "@tanstack/react-query";
import type { Evidence, Incident } from "@contract";
import { ackIncident } from "../../api/incidents";
import { useStream } from "../../store/stream";
import { useUi } from "../../store/ui";
import { formatDelay, formatDelayApprox, formatDelayShort } from "../../lib/format";
import { arrivalLabel, formatSimTime, minutesBetween } from "../../lib/time";
import { stopAfterWord } from "../../lib/labels";
import { RiskGlyph } from "../map/RiskGlyph";
import { WhatIf } from "./WhatIf";

const STATUS_LABEL = { open: "новый", ack: "принят", resolved: "закрыт" } as const;

const OUTCOME_TEXT = {
  pending: "",
  hit: "подтвердилось",
  false_alarm: "не подтвердилось",
  miss: "пропуск",
} as const;

function addSeconds(naive: string, sec: number): string {
  return new Date(new Date(naive + "Z").getTime() + sec * 1000).toISOString().slice(0, 19);
}

/** Остановка во фразе: «Каширское ш., д.23» в кавычках; «Остановка без адреса» → «без адреса» без кавычек. */
function stopInSentence(name: string): string {
  const rest = stopAfterWord(name);
  return rest === name ? `«${name}»` : rest;
}

/** Итог закрытого инцидента: «ТС …: прогноз был +2:45. Факт: опоздание +4:23 — подтвердилось.» */
function outcomeSentence(inc: Incident): string {
  const actual = inc.actual_delay_s;
  if (inc.outcome === "pending" || actual == null) return "Инцидент закрыт, факт прибытия не получен.";
  const fact = actual < 0 ? `опережение ${formatDelayShort(actual).slice(1)}` : `опоздание ${formatDelayShort(actual)}`;
  const forecast = `ТС ${inc.vehicle_id}, остановка ${stopInSentence(inc.target_stop.name)}: прогноз был ${formatDelayShort(inc.predicted_delay_s)}.`;
  return `${forecast} Факт: ${fact} — ${OUTCOME_TEXT[inc.outcome]}.`;
}

/** Вывод карточки одной фразой: кто, насколько, где и когда; для закрытого — итог по факту. */
function summarySentence(inc: Incident, eta: string | null): string {
  if (inc.status === "resolved") return outcomeSentence(inc);
  const planned = inc.target_stop.planned_at;
  const early = inc.predicted_delay_s < 0;
  const how = early ? "придёт раньше" : "опоздает";
  const times = planned && eta ? `: план ${formatSimTime(planned)} → прогноз ${formatSimTime(eta)}` : "";
  const chance = early ? "" : ` Вероятность опоздания ${Math.round(inc.p_late * 100)}%.`;
  return (
    `ТС ${inc.vehicle_id} ${how} на ${formatDelayApprox(inc.predicted_delay_s)} ` +
    `на остановку ${stopInSentence(inc.target_stop.name)}${times}.${chance}`
  );
}

function HorizonStrip({ inc, simTime }: { inc: Incident; simTime: string | null }) {
  const planned = inc.target_stop.planned_at;
  if (!planned) return null;
  const total = Math.max(minutesBetween(inc.created_at, planned), 1);
  const passed = simTime ? minutesBetween(inc.created_at, simTime) : 0;
  const pct = Math.min(100, Math.max(0, (passed / total) * 100));

  return (
    <div className="horizon">
      <div className="horizon-lead">
        <span>Предупредили</span>
        <span className="horizon-num num">за {inc.lead_min} мин</span>
        <span>до прибытия</span>
      </div>
      <div className="horizon-track">
        <div className="horizon-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="horizon-labels num">
        <span>алерт {formatSimTime(inc.created_at)}</span>
        <span>прибытие по плану {formatSimTime(planned)}</span>
      </div>
    </div>
  );
}

function EvidenceBars({ items }: { items: Evidence[] }) {
  const max = Math.max(1, ...items.map((e) => Math.abs(e.contribution_s ?? 0)));
  return (
    <ul className="evidence">
      {items.map((e) => (
        <li key={e.feature}>
          <div className="ev-top">
            <span className="dim">{e.label}</span>
            <span className="num">{e.value}</span>
          </div>
          <div className="ev-track">
            <div className="ev-bar" style={{ width: `${(Math.abs(e.contribution_s ?? 0) / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function IncidentCard({ id }: { id: string }) {
  const inc = useStream((s) => s.incidents[id]);
  const simTime = useStream((s) => s.simTime);
  const selectIncident = useUi((s) => s.selectIncident);

  const ack = useMutation({
    mutationFn: (code: string) => ackIncident(id, code),
    onSuccess: (updated) => useStream.getState().upsertIncident(updated),
  });

  if (!inc) {
    return (
      <section className="card">
        <button className="card-back" onClick={() => selectIncident(null)}>← Все инциденты</button>
        <p className="dim">Инцидент больше не доступен.</p>
      </section>
    );
  }

  const resolved = inc.status === "resolved";
  const planned = inc.target_stop.planned_at;
  const eta = planned ? addSeconds(planned, inc.predicted_delay_s) : null;
  const accepted = inc.ack?.action_code;

  return (
    <section className={`card risk-${inc.risk_level}`} aria-label="Карточка инцидента">
      <button className="card-back" onClick={() => selectIncident(null)}>← Все инциденты</button>

      {/* 1. Какое ТС */}
      <header className="card-head">
        <span className="card-kicker">Маршрут</span>
        <div className="card-title">
          <RiskGlyph risk={inc.risk_level} size={20} />
          <h2>{inc.route_name ?? "не указан"}</h2>
        </div>
        <div className="card-meta">
          <span className="num">ТС {inc.vehicle_id}</span>
          <span className={`chip chip-${inc.status}`}>{STATUS_LABEL[inc.status]}</span>
          <button className="card-link" onClick={() => useUi.getState().selectVehicle(inc.vehicle_id)}>
            Подробнее о ТС →
          </button>
        </div>
      </header>

      {/* Вывод одной фразой — понятен без остальной карточки */}
      <p className={`card-summary${resolved ? ` outcome-${inc.outcome}` : ""}`}>{summarySentence(inc, eta)}</p>

      {/* 2. Прогноз */}
      <div className="card-block">
        <div className="forecast-main">
          <span className="forecast-label dim small">
            {resolved ? "прогноз был" : inc.predicted_delay_s < 0 ? "прогноз опережения" : "прогноз опоздания"}
          </span>
          <span className="forecast-delay num">{formatDelay(inc.predicted_delay_s)}</span>
          <span className="forecast-err dim num" title="ожидаемая ошибка прогноза">
            ±{Math.round(inc.expected_abs_error_s)} с
          </span>
        </div>
        <div className="forecast-sub">
          {!resolved && <b>{arrivalLabel(simTime, planned, "прибытие по плану")}</b>}
          <span>
            вероятность опоздания <b className="num">{Math.round(inc.p_late * 100)}%</b>
          </span>
        </div>
        <div className="forecast-stop">
          <span className="dim small">Остановка</span>
          <span>{inc.target_stop.name}</span>
          <span className="num small">
            план {planned ? formatSimTime(planned) : "—"} → прогноз {eta ? formatSimTime(eta, true) : "—"}
          </span>
        </div>
        <HorizonStrip inc={inc} simTime={simTime} />
      </div>

      {/* 3. Причина */}
      <div className="card-block">
        <h3>Причина</h3>
        <p className="cause-title">{inc.cause.title}</p>
        <p className="dim small">{inc.cause.details}</p>
        {inc.cause.evidence.length > 0 && (
          <>
            <h4 className="ev-head">Почему модель так считает</h4>
            {inc.cause.evidence.some((e) => e.contribution_s != null) && (
              <p className="dim small">длина полосы — вклад признака в прогноз</p>
            )}
            <EvidenceBars items={inc.cause.evidence} />
          </>
        )}
      </div>

      {/* 4. Участок */}
      <div className="card-block">
        <h3>Участок</h3>
        {inc.segment ? (
          <p>
            {inc.segment.from_stop.name} → {inc.segment.to_stop.name}
          </p>
        ) : (
          <p>перед остановкой {stopAfterWord(inc.target_stop.name)}</p>
        )}
        <p className="dim small">Подсвечен на карте белой обводкой</p>
      </div>

      {/* 5. Что сделать */}
      {inc.recommendations.length > 0 && (
        <div className="card-block">
          <h3>Что сделать</h3>
          <ul className="recs">
            {inc.recommendations.map((r) => (
              <li key={r.code} className={accepted === r.code ? "is-accepted" : ""}>
                <div>
                  <div className="rec-title">{r.title}</div>
                  <div className="dim small">{r.description}</div>
                </div>
                {accepted === r.code ? (
                  <span className="chip chip-ack">принято</span>
                ) : (
                  !resolved &&
                  !accepted && (
                    <button className="btn-primary" disabled={ack.isPending} onClick={() => ack.mutate(r.code)}>
                      Принять
                    </button>
                  )
                )}
              </li>
            ))}
          </ul>
          {ack.isError && <p className="error small">Не удалось отправить. Попробуйте ещё раз.</p>}
        </div>
      )}

      {/* 6. Что если: оценка меры до её принятия */}
      {!resolved && <WhatIf key={inc.id} inc={inc} />}
    </section>
  );
}
