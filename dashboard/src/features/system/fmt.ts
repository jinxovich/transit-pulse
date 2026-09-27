// Числа панели «Система»: пусто или ноль измерения — «—», табличные цифры по-русски.
const DASH = "—";

function fixed(v: number, digits: number): string {
  return v.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/** Задержка в мс: доли для быстрых этапов, целые для медленных. */
export function fmtMs(v: number | null | undefined): string {
  if (v == null || v === 0) return DASH;
  if (v < 1) return fixed(v, 2);
  if (v < 10) return fixed(v, 1);
  return fixed(Math.round(v), 0);
}

/** Измеряемая величина (п/с, MAE): ноль до первых данных — «—». */
export function fmtValue(v: number | null | undefined, digits = 0): string {
  if (v == null || v === 0) return DASH;
  return fixed(v, digits);
}

/** Счётчик событий (ошибки, реконнекты): ноль — осмысленное значение. */
export function fmtCount(v: number | null | undefined): string {
  if (v == null) return DASH;
  return v.toLocaleString("ru-RU");
}

export function fmtPct(share: number | null | undefined): string {
  if (share == null) return DASH;
  return `${Math.round(share * 100)}%`;
}

/** Возраст в секундах: ноль — «только что пришёл», а не пусто. */
export function fmtAge(v: number | null | undefined): string {
  if (v == null) return DASH;
  return fixed(v, 1);
}
