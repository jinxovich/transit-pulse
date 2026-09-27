import type { RiskLevel, StreamMode, Thresholds } from "@contract";

export const MODE_LABEL: Record<StreamMode, string> = {
    LIVE: "Поток идёт",
    WARMING_UP: "Прогрев",
    DEGRADED: "Поток прерван",
    OFFLINE: "Нет источника",
    PAUSED: "Пауза",
};

/** Короткая подпись уровня риска ТС: в счётчиках верхней панели и в легенде карты. */
export const RISK_LABEL: Record<RiskLevel, string> = {
    red: "опаздывают",
    yellow: "риск",
    green: "в графике",
    early: "раньше",
    none: "нет прогноза",
};

const min = (s: number) => `${Math.round(Math.abs(s) / 6) / 10} мин`;
const pct = (p: number) => `${Math.round(p * 100)}%`;

/** Расшифровка уровня риска для подсказки: пороги из /config, как в risk_of на бэкенде. */
export function riskHint(risk: RiskLevel, th?: Thresholds): string {
    const name = `${RISK_LABEL[risk][0].toUpperCase()}${RISK_LABEL[risk].slice(1)}`;
    if (!th) return name;
    switch (risk) {
    case "red":
        return `${name}: прогноз опоздания больше ${min(th.red_delay_s)} или вероятность ≥ ${pct(th.red_p_late)}`;
    case "yellow":
        return `${name}: прогноз опоздания от ${min(th.yellow_delay_s)} или вероятность ≥ ${pct(th.yellow_p_late)}`;
    case "green":
        return `${name}: прогноз в пределах графика`;
    case "early":
        return `${name}: опережение графика больше ${min(th.early_delay_s)}`;
    default:
        return `${name}: ТС без расписания или без свежих данных`;
    }
}

/** Название остановки после слова «остановка»: «№ 7 рейса» вместо «Остановка № 7 рейса». */
export const stopAfterWord = (name: string) => name.replace(/^Остановка\s+/, "");
