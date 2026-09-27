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
