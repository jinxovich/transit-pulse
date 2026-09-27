import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { AppConfig, Incident, IncidentOutcome, IncidentStatus } from "@contract";
import { fetchConfig } from "../../api/config";
import { useStream } from "../../store/stream";
import { useUi } from "../../store/ui";
import { formatDelay, formatMinutes } from "../../lib/format";
import { arrivalLabel, minutesBetween } from "../../lib/time";
import { RiskGlyph } from "../map/RiskGlyph";

/** Что считается инцидентом — одной строкой под заголовком; пороги из /config (по умолчанию 2 мин, 50%, 10–15 мин). */
function feedRule(config: AppConfig | undefined): string {
    const delay = formatMinutes(config?.thresholds.red_delay_s ?? 120);
    const pLate = Math.round((config?.thresholds.red_p_late ?? 0.5) * 100);
    const [from, to] = config?.horizon_min ?? [10, 15];
    return `Прогноз опоздания > ${delay} (или вероятность ≥ ${pLate}%) на остановке за ${from}–${to} мин до прибытия`;
}

const STATUS_LABEL: Record<IncidentStatus, string> = {
    open: "новый",
    ack: "принят",
    resolved: "закрыт",
    };

    const OUTCOME_LABEL: Record<IncidentOutcome, string> = {
    pending: "ждём событие",
    hit: "опоздание подтвердилось",
    false_alarm: "не подтвердилось",
    miss: "пропуск",
    };

    /** Порядок по ТЗ: открытые выше закрытых; среди открытых красные выше; дальше — чьё событие раньше. */
    function sortIncidents(list: Incident[], simTime: string | null): Incident[] {
    const minutesLeft = (i: Incident) =>
        simTime && i.target_stop.planned_at ? minutesBetween(simTime, i.target_stop.planned_at) : Infinity;

    return [...list].sort((a, b) => {
        const aDone = a.status === "resolved" ? 1 : 0;
        const bDone = b.status === "resolved" ? 1 : 0;
        if (aDone !== bDone) return aDone - bDone;
        if (aDone) return b.updated_at.localeCompare(a.updated_at); // закрытые: свежие сверху

        const aRed = a.risk_level === "red" ? 0 : 1;
        const bRed = b.risk_level === "red" ? 0 : 1;
        if (aRed !== bRed) return aRed - bRed;

        return minutesLeft(a) - minutesLeft(b);
    });
    }

/** Подпись к числу: у открытых — прогноз опоздания/опережения, у закрытых — каким был прогноз (факт ниже). */
function delayLabel(inc: Incident): string {
    if (inc.status === "resolved") return "прогноз был";
    return inc.predicted_delay_s < 0 ? "опережение" : "опоздание";
}

function FeedItem({ inc, simTime }: { inc: Incident; simTime: string | null }) {
    const selectIncident = useUi((s) => s.selectIncident);
    const resolved = inc.status === "resolved";

    return (
        <li>
        <button
            className={`feed-item risk-${inc.risk_level}${resolved ? " is-resolved" : ""}`}
            onClick={() => selectIncident(inc.id)}
        >
            <div className="feed-row1">
            <RiskGlyph risk={resolved ? "none" : inc.risk_level} size={16} />
            <span className="feed-label">{delayLabel(inc)}</span>
            <span className="feed-delay num">{formatDelay(inc.predicted_delay_s)}</span>
            <span className={`chip chip-${inc.status}`}>{STATUS_LABEL[inc.status]}</span>
            </div>
            {!resolved && (
            <div className="feed-until">{arrivalLabel(simTime, inc.target_stop.planned_at)}</div>
            )}
            <div className="feed-row2">
            <span className="feed-route" title={inc.route_name ?? undefined}>
                <span className="feed-label">маршрут</span> {inc.route_name ?? "не указан"}
            </span>
            <span className="feed-vid num">ТС {inc.vehicle_id}</span>
            </div>
            <div className="feed-row3">
            {resolved && inc.outcome !== "pending"
                ? `Факт ${formatDelay(inc.actual_delay_s ?? 0)}: ${OUTCOME_LABEL[inc.outcome]}`
                : inc.cause.title}
            </div>
        </button>
        </li>
    );
    }

    export function IncidentFeed() {
    const incidents = useStream((s) => s.incidents);
    const simTime = useStream((s) => s.simTime);
    const config = useQuery({ queryKey: ["config"], queryFn: fetchConfig, staleTime: Infinity }).data;

    const sorted = useMemo(() => sortIncidents(Object.values(incidents), simTime), [incidents, simTime]);
    const active = sorted.filter((i) => i.status !== "resolved");
    const done = sorted.filter((i) => i.status === "resolved");

    return (
        <section className="feed" aria-label="Инциденты">
        <header className="feed-head">
            <h2>Инциденты</h2>
            <span className="feed-count num">{active.length} открыто</span>
        </header>
        <p className="feed-rule">{feedRule(config)}</p>

        {active.length === 0 ? (
            <div className="feed-empty">
            <RiskGlyph risk="green" size={18} />
            <div>
                <div>Инцидентов нет — все ТС в графике</div>
                <div className="dim small">Алерт появится здесь за 10–15 мин до опоздания</div>
            </div>
            </div>
        ) : (
            <ul className="feed-list">
            {active.map((i) => (
                <FeedItem key={i.id} inc={i} simTime={simTime} />
            ))}
            </ul>
        )}

        {done.length > 0 && (
            <>
            <h3 className="feed-sub">Закрытые</h3>
            <ul className="feed-list">
                {done.map((i) => (
                <FeedItem key={i.id} inc={i} simTime={simTime} />
                ))}
            </ul>
            </>
        )}
        </section>
    );
}