import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import type { AppConfig } from "@contract";
import { startStream } from "./api/ws";
import { TopBar } from "./features/topbar/TopBar";

async function fetchConfig(): Promise<AppConfig> {
  const res = await fetch("/api/v1/config");
  if (!res.ok) throw new Error(`config: ${res.status}`);
  return res.json();
}

export default function App() {
  useEffect(() => startStream(), []);

  const config = useQuery({
    queryKey: ["config"],
    queryFn: fetchConfig,
    staleTime: Infinity,
    retry: true,
  });

  useEffect(() => {
    if (!config.data) return;
    for (const [name, color] of Object.entries(config.data.colors)) {
      document.documentElement.style.setProperty(`--risk-${name}`, color);
    }
  }, [config.data]);

  return (
    <div className="app">
      <TopBar />
      <main className="main">
        <div className="stage">
          <p className="placeholder">Здесь будет карта</p>
        </div>
        <aside className="side">
          <p className="placeholder">Здесь будет лента инцидентов</p>
        </aside>
      </main>
    </div>
  );
}
