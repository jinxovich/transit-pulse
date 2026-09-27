import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchConfig } from "../../api/config";
import { useStream } from "../../store/stream";
import { formatSimTime, simMs } from "../../lib/time";

const TICK_MS = 250;
/** Сообщения перестали приходить — дальше этого не досчитываем, ждём сервер. */
const MAX_AHEAD_WALL_MS = 1500;
/** Откат больше минуты сим-времени — новая сессия или перемотка, часы сбрасываются. */
const RESET_BACK_MS = 60_000;

/** Сим-время сейчас: последнее от сервера плюс досчёт по скорости, не больше MAX_AHEAD. */
function estimate(speed: number): number | null {
    const { simTime, simWall } = useStream.getState();
    if (!simTime) return null;
    return simMs(simTime) + Math.min(performance.now() - simWall, MAX_AHEAD_WALL_MS) * speed;
}

/**
 * Сим-часы, которые идут плавно. Сервер присылает sim_time примерно раз в секунду, а при
 * ускоренном проигрывании (×30) это рывки по 30 с. Между сообщениями время досчитывается по
 * скорости сессии; часы не идут назад и стоят, когда поток на паузе или прерван.
 */
export function SimClock() {
    const simTime = useStream((s) => s.simTime);
    const mode = useStream((s) => s.status?.mode);
    const speed = useQuery({
        queryKey: ["sim-speed"],
        queryFn: fetchConfig,
        refetchInterval: 10_000,
        select: (c) => c.sim_speed,
    }).data ?? 1;
    const [smooth, setSmooth] = useState<number | null>(null);

    const running = mode === "LIVE" || mode === "WARMING_UP";
    useEffect(() => {
        if (!running) return;
        const id = window.setInterval(() => {
            const est = estimate(speed);
            setSmooth((prev) =>
                est === null || prev === null || est < prev - RESET_BACK_MS ? est : Math.max(prev, est),
            );
        }, TICK_MS);
        return () => window.clearInterval(id);
    }, [running, speed]);

    const server = simTime ? simMs(simTime) : null;
    const shown = running && smooth !== null && server !== null && smooth >= server - RESET_BACK_MS
        ? Math.max(smooth, server)
        : server;
    const text = shown === null
        ? "--:--:--"
        : formatSimTime(new Date(shown).toISOString().slice(0, 19), true);
    return (
        <div className="clock num" title="Время датасета (сим-время потока)">
            {text}
        </div>
    );
}
