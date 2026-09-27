import type { StreamMode } from "@contract";

export const MODE_LABEL: Record<StreamMode, string> = {
    LIVE: "Поток идёт",
    WARMING_UP: "Прогрев",
    DEGRADED: "Поток прерван",
    OFFLINE: "Нет источника",
    PAUSED: "Пауза",
};
