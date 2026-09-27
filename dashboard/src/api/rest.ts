/** GET по относительному URL. Ошибка несёт `detail` бэкенда или код ответа. */
export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
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
