import { useStream } from "../../store/stream";
import {formatSimTime } from "../../lib/time"
import type { StreamMode } from "@contract";
import type { RiskLevel } from "@contract";
import { RiskGlyph } from "../map/RiskGlyph";


const MODE_LABEL: Record<StreamMode, string> = {
    LIVE: "Поток идёт",
    WARMING_UP: "Прогрев",
    DEGRADED: "Поток прерван",
    OFFLINE: "Нет источника",
    PAUSED: "Пауза",
};
const COUNTERS: RiskLevel[] = ["red", "yellow", "green", "early"];

function StreamBadge(){
    const connected = useStream((s) => s.connected);
    const mode = useStream((s) => s.status?.mode);
    const current = mode ?? "WARMING_UP";
    const shown = connected ? current : "OFFLINE";
    const label = connected ? MODE_LABEL[current] : "Нет связи";
    return(
        <div className={`badge badge-${shown.toLowerCase()}`}>
            <span className="badge-dot" />
            {label}
        </div>
        
    )
}

export function TopBar() {
    const simTime = useStream((s) => s.simTime);
    const kpis = useStream((s) => s.kpis);

    return (
        <header className="topbar">
        <div className="brand">Transit Pulse</div>
        <div className="clock num">
            {simTime ? formatSimTime(simTime, true) : "--:--:--"}
        </div>
        <StreamBadge />

        <div className="kpi-group">
            {COUNTERS.map((risk) => {
            const n = kpis?.by_risk[risk] ?? 0;
            const alarm = risk === "red" && n > 0;
            return (
                <div key={risk} className={`kpi-risk${alarm ? " is-alarm" : ""}`}>
                <RiskGlyph risk={risk} size={risk === "red" ? 18 : 15} />
                <span className="num">{n}</span>
                </div>
            );
            })}
        </div>

        <div className="kpi-stat">
            <span className="kpi-label">ТС на линии</span>
            <span className="num">
            {kpis?.vehicles_online ?? "—"}
            <span className="kpi-of">/{kpis?.vehicles_total ?? "—"}</span>
            </span>
        </div>

        <div className={`kpi-stat${kpis?.incidents_open ? " kpi-stat-alarm" : ""}`}>
            <span className="kpi-label">Инцидентов</span>
            <span className="num">{kpis?.incidents_open ?? "—"}</span>
        </div>
        </header>
    );
}