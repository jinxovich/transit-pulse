import type { ReplayControl, SimClock } from "@contract";

/** POST /api/v1/replay/control: пауза, продолжение, скорость проигрывания дня. */
export async function controlReplay(body: ReplayControl): Promise<SimClock> {
    const res = await fetch("/api/v1/replay/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`Replayer: ${res.status}`);
    return res.json();
}
