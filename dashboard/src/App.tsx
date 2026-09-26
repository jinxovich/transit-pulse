import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {fetchConfig} from "./api/config"
import { startStream } from "./api/ws";
import { TopBar } from "./features/topbar/TopBar";
import {MapView} from "./features/map/MapView";
import { IncidentFeed } from "./features/incidents/IncidentFeed";
import { IncidentCard } from "./features/incidents/IncidentCard";
import { useUi } from "./store/ui";
import { ConnectionBanner, StatusBanner } from "./features/banners/Banners";

export default function App() {
  useEffect(() => startStream(), []);
  const selectedId = useUi((s) => s.selectedIncidentId);

  // Esc закрывает карточку.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") useUi.getState().selectIncident(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
          <div className="banners">
            <ConnectionBanner />
            <StatusBanner />
          </div>
          <MapView />
        </div>
        <aside className="side">
          {selectedId ? <IncidentCard key={selectedId} id={selectedId} /> : <IncidentFeed />}
        </aside>
      </main>
    </div>
  );
}
