const asUtc = (naive: string) => new Date(naive + "Z");
export const formatSimTime = (naive: string, withSeconds = false) =>
    asUtc(naive).toLocaleTimeString("ru-RU", {
    timeZone: "UTC", hour: "2-digit", minute: "2-digit", ...(withSeconds && { second: "2-digit" }),
    });
export const minutesBetween = (fromNaive: string, toNaive: string) =>
    (asUtc(toNaive).getTime() - asUtc(fromNaive).getTime()) / 60000;

/** Сим-время в миллисекундах (для осей графиков) и обратно, без сдвига на пояс браузера. */
export const simMs = (naive: string) => asUtc(naive).getTime();
export const fromSimMs = (ms: number) => new Date(ms).toISOString().slice(0, 19);

export function untilLabel(simTime: string | null, target: string | null | undefined): string {
    if (!simTime || !target) return "";
    const m = minutesBetween(simTime, target);
    if (m <= 0) {
        const ago = Math.floor(-m);
        return ago < 1 ? "сейчас" : `${ago} мин назад`;
    }
    return m < 1 ? "меньше минуты" : `через ${Math.floor(m)} мин`;
}
