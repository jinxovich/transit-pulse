import { useStream } from "../../store/stream";
import {formatSimTime } from "../../lib/time"
import type { RiskLevel } from "@contract";
import { RiskGlyph } from "../map/RiskGlyph";
import { MODE_LABEL, RISK_LABEL, riskHint } from "../../lib/labels";
import { useUi } from "../../store/ui";
import { useQuery } from "@tanstack/react-query";
import { fetchConfig } from "../../api/config";

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

function SystemButton() {
    const open = useUi((s) => s.systemOpen);
    const ml = useStream((s) => s.status?.ml_status);
    return (
        <button
            className={`sys-btn${open ? " is-open" : ""}`}
            aria-pressed={open}
            onClick={() => useUi.getState().setSystemOpen(!open)}
        >
            <span className={`sys-dot ${!ml || ml === "ok" ? "is-ok" : "is-bad"}`} aria-hidden />
            Система
        </button>
    );
}

export function TopBar() {
    const simTime = useStream((s) => s.simTime);
    const kpis = useStream((s) => s.kpis);
    const thresholds = useQuery({ queryKey: ["config"], queryFn: fetchConfig, staleTime: Infinity }).data?.thresholds;

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
            const hint = `${riskHint(risk, thresholds)}. ТС: ${n}`;
            return (
                <div key={risk} className={`kpi-risk${alarm ? " is-alarm" : ""}`} title={hint}>
                <RiskGlyph risk={risk} size={risk === "red" ? 18 : 15} />
                <span className="num">{n}</span>
                <span className="kpi-risk-label">{RISK_LABEL[risk]}</span>
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

        <SystemButton />
        </header>
    );
}