import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {fetchConfig} from "./api/config"
import { startStream } from "./api/ws";
import { TopBar } from "./features/topbar/TopBar";
import {MapView} from "./features/map/MapView"


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
          <MapView/>
        </div>
        <aside className="side">
          <p className="placeholder">Здесь будет лента инцидентов</p>
        </aside>
      </main>
    </div>
  );
}
