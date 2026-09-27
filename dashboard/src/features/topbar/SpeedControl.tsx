import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ReplayControl } from "@contract";
import { fetchConfig } from "../../api/config";
import { controlReplay } from "../../api/replay";
import { useStream } from "../../store/stream";

/** ×1 — день идёт в реальном времени, остальное — ускоренный просмотр. */
const SPEEDS = [1, 10, 30, 60];
/** На узком экране остаются только ×1 и ×30. */
const EXTRA = new Set([10, 60]);

export function SpeedControl() {
    const qc = useQueryClient();
    const speed = useQuery({
        queryKey: ["sim-speed"],
        queryFn: fetchConfig,
        refetchInterval: 10_000,
        select: (c) => c.sim_speed,
    }).data;
    const paused = useStream((s) => s.status?.mode === "PAUSED");
    const send = useMutation({
        mutationFn: (body: ReplayControl) => controlReplay(body),
        onSettled: () => qc.invalidateQueries({ queryKey: ["sim-speed"] }),
    });

    return (
        <div className="speed-ctl" role="group" aria-label="Скорость проигрывания дня">
            <button
                className="speed-btn speed-pause"
                title={paused ? "Продолжить поток" : "Пауза: поток и часы остановятся"}
                aria-label={paused ? "Продолжить" : "Пауза"}
                disabled={send.isPending}
                onClick={() => send.mutate({ action: paused ? "resume" : "pause", speed: null, seek_to: null })}
            >
                {paused ? "▶" : "❚❚"}
            </button>
            {SPEEDS.map((s) => (
                <button
                    key={s}
                    className={`speed-btn num${speed === s ? " is-active" : ""}${EXTRA.has(s) ? " is-extra" : ""}`}
                    aria-pressed={speed === s}
                    title={s === 1 ? "Реальное время" : `День проигрывается в ${s} раз быстрее`}
                    disabled={send.isPending}
                    onClick={() => send.mutate({ action: "speed", speed: s, seek_to: null })}
                >
                    ×{s}
                </button>
            ))}
        </div>
    );
}
