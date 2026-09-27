// Числа панели «Система»: пусто или ноль измерения — «—», табличные цифры по-русски.
import type { AlertPolicyInfo } from "@contract";

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

/** Коэффициент, где ноль осмыслен (алертов на ТС·ч, Brier). */
export function fmtRatio(v: number | null | undefined, digits = 2): string {
  if (v == null) return DASH;
  return fixed(v, digits);
}

/** «1 проход», «2 прохода», «5 проходов». */
export function pluralRu(n: number, one: string, few: string, many: string): string {
  const d10 = n % 10;
  const d100 = n % 100;
  if (d10 === 1 && d100 !== 11) return one;
  if (d10 >= 2 && d10 <= 4 && (d100 < 12 || d100 > 14)) return few;
  return many;
}

/** Политика алерта словами: «задержка > 150 с или вероятность ≥ 60%, 2 прохода подряд». */
export function fmtPolicy(p: AlertPolicyInfo): string {
  const join = p.mode === "and" ? "и" : "или";
  const streak =
    p.min_streak > 1 ? `${p.min_streak} ${pluralRu(p.min_streak, "проход", "прохода", "проходов")} подряд` : "с первого прохода";
  return `задержка > ${Math.round(p.delay_s)} с ${join} вероятность ≥ ${Math.round(p.p_late * 100)}%, ${streak}`;
}
