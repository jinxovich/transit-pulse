import { useStream } from "../../store/stream";
import {formatSimTime } from "../../lib/time"
import type { StreamMode } from "@contract";

const MODE_LABEL: Record<StreamMode, string> = {
    LIVE: "Поток идёт",
    WARMING_UP: "Прогрев",
    DEGRADED: "Поток прерван",
    OFFLINE: "Нет источника",
    PAUSED: "Пауза",
};

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

export function TopBar(){
    const simTime = useStream((s) => s.simTime);
    return(
        <header className ="topbar">
            <div className ="brand">Transit Pulse</div>
            <div className ="clock num">
                {simTime ? formatSimTime(simTime, true) : "--:--:--"}
            </div>
            <StreamBadge/>
        </header>
    )
}