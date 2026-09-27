import type { VehicleDetail } from "@contract";
import { getJson } from "./rest";

/** GET /api/v1/vehicles/{id}: нитка отклонения, прогноз и остановки ±60 мин. */
export const fetchVehicleDetail = (id: string) =>
  getJson<VehicleDetail>(`/api/v1/vehicles/${encodeURIComponent(id)}`);
