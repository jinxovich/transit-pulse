import type { Incident } from "@contract";

export async function ackIncident(id: string, actionCode: string): Promise<Incident> {
  const res = await fetch(`/api/v1/incidents/${encodeURIComponent(id)}/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action_code: actionCode, comment: null }),
  });
  if (!res.ok) throw new Error(`Ошибка ${res.status}`);
  return res.json();
}
