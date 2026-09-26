import type { WsMessage } from "@contract";
import { useStream } from "../store/stream";

const BACKOFF = [500, 1000, 2000, 5000, 10000];

function onMessage(msg: WsMessage) {
    const s = useStream.getState();
    s.touch(msg.sim_time);
    if (msg.type === "snapshot") return s.replaceAll(msg);
    if (msg.session_id !== s.sessionId) return;
    switch (msg.type) {
        case "vehicles.delta": return s.upsertVehicles(msg.data.vehicles, msg.data.removed);
        case "incident.opened":
        case "incident.updated":
        case "incident.resolved": return s.upsertIncident(msg.data);
        case "kpis": return s.setKpis(msg.data);
        case "system.status": return s.setStatus(msg.data);
    }
    }

    export function startStream() {
    let ws: WebSocket | null = null;
    let attempt = 0;
    let stopped = false;
    let timer: number | undefined;
    let watchdog: number | undefined;
    let lastAt = 0;

    function connect() {
        if (stopped) return;
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const sock = new WebSocket(`${proto}://${location.host}/ws/v1/stream`);
        ws = sock;

        sock.onopen = () => {
        attempt = 0;
        lastAt = performance.now();
        useStream.getState().setConnected(true);
        watchdog = window.setInterval(() => {
            if (performance.now() - lastAt > 15000) sock.close();
        }, 2000);
        };
        sock.onmessage = (e) => {
        lastAt = performance.now();
        onMessage(JSON.parse(e.data));
        };
        sock.onclose = () => {
        window.clearInterval(watchdog);
        useStream.getState().setConnected(false);
        if (stopped || ws !== sock) return;
        timer = window.setTimeout(connect, BACKOFF[Math.min(attempt++, BACKOFF.length - 1)]);
        };
    }

    timer = window.setTimeout(connect, 0);
    return () => {
        stopped = true;
        window.clearTimeout(timer);
        window.clearInterval(watchdog);
        ws?.close();
    };
}