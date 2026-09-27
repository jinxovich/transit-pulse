export function formatDelay(sec: number): string {
    const sign = sec >= 0 ? "+" : "−";
    const total = Math.round(Math.abs(sec));
    const m = Math.floor(total / 60), s = total % 60;
    if (m === 0) return `${sign}${s} с`;
    return s ? `${sign}${m} мин ${s} с` : `${sign}${m} мин`;
}

/** Компактно для таблиц: «+2:19», «−0:45». */
export function formatDelayShort(sec: number): string {
    const sign = sec >= 0 ? "+" : "−";
    const total = Math.round(Math.abs(sec));
    return `${sign}${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

const APPROX_SECONDS_BELOW = 45;

/** Модуль отклонения для фразы: «≈2,5 мин» (до 0,5 мин) или «40 с», если меньше 45 с. */
export function formatDelayApprox(sec: number): string {
    const abs = Math.abs(sec);
    if (abs < APPROX_SECONDS_BELOW) return `${Math.round(abs)} с`;
    return `≈${String(Math.round(abs / 30) / 2).replace(".", ",")} мин`;
}

/** Длительность в минутах без знака: «2 мин», «1,5 мин». */
export function formatMinutes(sec: number): string {
    return `${String(Math.round(Math.abs(sec) / 6) / 10).replace(".", ",")} мин`;
}
