import type { VehicleState } from "@contract";
import { formatDelay } from "../../lib/format";
import { formatSimTime, minutesBetween } from "../../lib/time";

/** Защита от HTML внутри данных*/
const esc = (s: string) =>
    s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

    /** при наведении на ТС. */
    export function tooltipHtml(v: VehicleState, simTime: string | null): string {
    const rows: string[] = [];

    const title = v.kind === "unknown" ? "Неопознанный борт" : v.route_name ?? "Без маршрута";
    const speed = v.speed_kmh != null ? `, ${Math.round(v.speed_kmh)} км/ч` : "";
    rows.push(`<div class="tt-title">${esc(title)}</div>`);
    rows.push(`<div class="tt-sub">ТС ${esc(v.vehicle_id)}${speed}</div>`);

    if (v.stale && v.last_seen && simTime) {
        const min = Math.max(0, Math.floor(minutesBetween(v.last_seen, simTime)));
        rows.push(`<div class="tt-warn">Нет данных ${min} мин</div>`);
    }
    if (v.kind === "no_schedule") rows.push(`<div class="tt-dim">Нет расписания — только позиция</div>`);

    if (v.current_dev_s != null) {
        rows.push(`<div>Сейчас <b>${formatDelay(v.current_dev_s)}</b> к графику</div>`);
    }

    const p = v.prediction;
    if (p) {
        const at = p.target_stop.planned_at;
        const until = at && simTime ? Math.floor(minutesBetween(simTime, at)) : null;
        rows.push(
        `<div>Прогноз <b style="color: var(--risk-${p.risk_level})">${formatDelay(p.predicted_delay_s)}</b>` +
            ` на ост. ${esc(p.target_stop.name)}` +
            (at ? ` в ${formatSimTime(at)}` : "") +
            (until != null && until > 0 ? ` (через ${until} мин)` : "") +
            `</div>`,
        );
        if (p.model_mode === "fallback") rows.push(`<div class="tt-dim">упрощённый прогноз</div>`);
    }

    return rows.join("");
}