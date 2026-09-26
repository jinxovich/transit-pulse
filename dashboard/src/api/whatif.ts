import type { WhatIfAction, WhatIfResult } from "@contract";

/** POST /api/v1/whatif: прогноз ТС без меры и с мерой. Ошибка несёт `detail` бэкенда. */
export async function postWhatIf(
  vehicleId: string,
  action: WhatIfAction,
  value: number | null,
): Promise<WhatIfResult> {
  const res = await fetch("/api/v1/whatif", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ vehicle_id: vehicleId, action, value }),
  });
  if (!res.ok) {
    let detail = `Ошибка ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* тело не JSON — оставляем код ответа */
    }
    throw new Error(detail);
  }
  return res.json();
}
