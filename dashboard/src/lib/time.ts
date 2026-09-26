const asUtc = (naive: string) => new Date(naive + "Z");
export const formatSimTime = (naive: string, withSeconds = false) =>
    asUtc(naive).toLocaleTimeString("ru-RU", {
    timeZone: "UTC", hour: "2-digit", minute: "2-digit", ...(withSeconds && { second: "2-digit" }),
    });
export const minutesBetween = (fromNaive: string, toNaive: string) =>
    (asUtc(toNaive).getTime() - asUtc(fromNaive).getTime()) / 60000;
