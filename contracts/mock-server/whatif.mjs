// Mock-ответ POST /api/v1/whatif (WhatIfResult из contracts/ts/contract.ts).
//
// Настоящий бэкенд пересчитывает прогноз моделью; мок лишь сдвигает текущий прогноз ТС
// на величину меры, чтобы дашборд на моках показывал «было → стало» правдоподобно.

const DEFAULT_MIN = { hold_at_stop: 2, shorten_dwell: 3 };
const TITLES = {
  hold_at_stop: (m) => `Придержать на остановке ${m} мин`,
  shorten_dwell: (m) => `Сократить отстой на конечной на ${m} мин`,
  skip_layover: () => "Выпустить с конечной без отстоя",
  add_reserve: () => "Резервный выпуск на рейс после конечной",
};
const MOCK_LAYOVER_MIN = 5;
const P_PER_S = 1 / 300;

function riskOf(delay, pLate, th) {
  if (delay > th.red_delay_s || pLate >= th.red_p_late) return "red";
  if (delay < th.early_delay_s) return "early";
  if (delay >= th.yellow_delay_s || pLate >= th.yellow_p_late) return "yellow";
  return "green";
}

function shiftSeconds(action, minutes, delay) {
  if (action === "hold_at_stop") return minutes * 60;
  const late = Math.max(delay, 0);
  if (action === "shorten_dwell") return -Math.min(minutes * 60, MOCK_LAYOVER_MIN * 60, late);
  if (action === "skip_layover") return -Math.min(MOCK_LAYOVER_MIN * 60, late);
  return -late; // add_reserve: рейс после конечной по графику
}

function stopOf(pred, action, minutes, th) {
  const before = pred.predicted_delay_s;
  const shift = shiftSeconds(action, minutes, before);
  const after = Math.round((before + shift) * 10) / 10;
  const pAfter = Math.min(1, Math.max(0, pred.p_late + shift * P_PER_S));
  const ref = pred.target_stop;
  return {
    visit_id: ref.visit_id ?? "", stop_key: ref.stop_key, name: ref.name,
    planned_at: ref.planned_at ?? pred.generated_at, lead_min: pred.lead_min,
    in_window: pred.horizon_ok, after_break: action !== "hold_at_stop",
    predicted_before_s: before, predicted_after_s: after,
    p_late_before: pred.p_late, p_late_after: Math.round(pAfter * 1000) / 1000,
    risk_before: riskOf(before, pred.p_late, th), risk_after: riskOf(after, pAfter, th),
  };
}

/** WhatIfResult или { status, detail } для ошибки (404 / 409 / 422). */
export function mockWhatIf(vehicle, body, thresholds) {
  if (!vehicle) return { status: 404, detail: `ТС ${body.vehicle_id} не найдено` };
  const pred = vehicle.prediction;
  if (!pred) return { status: 409, detail: `У ТС ${body.vehicle_id} пока нет прогноза — оценить меру нельзя` };
  if (!(body.action in TITLES)) return { status: 422, detail: `Неизвестная мера ${body.action}` };
  const minutes = body.action in DEFAULT_MIN ? (body.value ?? DEFAULT_MIN[body.action]) : 0;
  const target = stopOf(pred, body.action, minutes, thresholds);
  const delta = Math.round((target.predicted_after_s - target.predicted_before_s) * 10) / 10;
  return {
    vehicle_id: body.vehicle_id, action: body.action,
    value_min: body.action === "skip_layover" ? MOCK_LAYOVER_MIN : minutes,
    title: TITLES[body.action](minutes),
    note: "Mock: прогноз сдвинут на величину меры без пересчёта моделью.",
    applied: delta !== 0, generated_at: pred.generated_at,
    model_version: pred.model_version, model_mode: pred.model_mode, current: pred, target,
    delta_delay_s: delta,
    delta_p_late: Math.round((target.p_late_after - target.p_late_before) * 1000) / 1000,
    stops: [target],
  };
}
