import { useState, type ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import type { Incident, RiskLevel, WhatIfAction, WhatIfResult } from "@contract";
import { postWhatIf } from "../../api/whatif";
import { formatDelay } from "../../lib/format";
import { formatSimTime } from "../../lib/time";
import { RiskGlyph } from "../map/RiskGlyph";

interface Measure {
  action: WhatIfAction;
  value: number | null;
  label: string;
  /** Код рекомендации инцидента, которой соответствует мера. */
  rec: string;
}

const MEASURES: Measure[] = [
  { action: "hold_at_stop", value: 2, label: "Придержать 2 мин", rec: "HOLD_AT_STOP" },
  { action: "shorten_dwell", value: 3, label: "Сократить отстой 3 мин", rec: "REDUCE_LAYOVER" },
  { action: "add_reserve", value: null, label: "Резервный выпуск", rec: "RESERVE_VEHICLE" },
];

const RISK_LABEL: Record<RiskLevel, string> = {
  green: "в графике",
  yellow: "риск",
  red: "опоздание",
  early: "раньше графика",
  none: "нет прогноза",
};

const pct = (p: number) => `${Math.round(p * 100)}%`;

function Risk({ risk }: { risk: RiskLevel }) {
  return (
    <span className="whatif-risk" style={{ color: `var(--risk-${risk})` }}>
      <RiskGlyph risk={risk} size={12} />
      {RISK_LABEL[risk]}
    </span>
  );
}

function Row({ label, before, after }: { label: string; before: ReactNode; after: ReactNode }) {
  return (
    <div className="whatif-row">
      <span className="dim">{label}</span>
      <span className="whatif-change num">
        {before}
        <span className="whatif-arrow" aria-label="станет">→</span>
        {after}
      </span>
    </div>
  );
}

function Outcome({ res }: { res: WhatIfResult }) {
  const t = res.target;
  const better = res.delta_delay_s < 0;
  return (
    <div className={`whatif-result ${res.applied ? (better ? "is-better" : "is-worse") : "is-same"}`}>
      <div className="whatif-title">{res.title}</div>
      <Row
        label="Прогноз опоздания"
        before={<span style={{ color: `var(--risk-${t.risk_before})` }}>{formatDelay(t.predicted_before_s)}</span>}
        after={<b style={{ color: `var(--risk-${t.risk_after})` }}>{formatDelay(t.predicted_after_s)}</b>}
      />
      <Row label="Вероятность опоздания" before={pct(t.p_late_before)} after={<b>{pct(t.p_late_after)}</b>} />
      <Row label="Уровень риска" before={<Risk risk={t.risk_before} />} after={<Risk risk={t.risk_after} />} />
      <p className="dim small">
        {t.name}, план {formatSimTime(t.planned_at)}. {res.note}
        {res.model_mode === "fallback" && " ML недоступен — оценка эвристикой."}
      </p>
    </div>
  );
}

/** «Что если…»: оценка упреждающей меры для ТС инцидента (POST /api/v1/whatif). */
export function WhatIf({ inc }: { inc: Incident }) {
  const [chosen, setChosen] = useState<WhatIfAction | null>(null);
  const run = useMutation({ mutationFn: (m: Measure) => postWhatIf(inc.vehicle_id, m.action, m.value) });
  const recommended = new Set(inc.recommendations.map((r) => r.code));

  return (
    <div className="card-block whatif">
      <h3>Что если…</h3>
      <div className="whatif-actions" role="group" aria-label="Упреждающие меры">
        {MEASURES.map((m) => (
          <button
            key={m.action}
            className={`whatif-btn${chosen === m.action ? " is-active" : ""}${recommended.has(m.rec) ? " is-recommended" : ""}`}
            aria-pressed={chosen === m.action}
            disabled={run.isPending}
            title={recommended.has(m.rec) ? "Соответствует рекомендации" : undefined}
            onClick={() => {
              setChosen(m.action);
              run.mutate(m);
            }}
          >
            {m.label}
          </button>
        ))}
      </div>
      {run.isPending && <p className="dim small">Пересчитываем прогноз с мерой…</p>}
      {run.isError && <p className="error small">{run.error.message}</p>}
      {run.data && !run.isPending && <Outcome res={run.data} />}
    </div>
  );
}
