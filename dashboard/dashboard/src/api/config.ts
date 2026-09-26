import type { AppConfig } from "@contract";

export async function fetchConfig(): Promise<AppConfig> {
    const res = await fetch("/api/v1/config");
    if (!res.ok) throw new Error(`config: ${res.status}`);
    return res.json();
}