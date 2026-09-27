import { lazy, Suspense, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {fetchConfig} from "./api/config"
import { startStream } from "./api/ws";
import { TopBar } from "./features/topbar/TopBar";
import {MapView} from "./features/map/MapView";
import { IncidentFeed } from "./features/incidents/IncidentFeed";
import { IncidentCard } from "./features/incidents/IncidentCard";
import { useUi } from "./store/ui";
import { ConnectionBanner, StatusBanner } from "./features/banners/Banners";
import { ErrorBoundary } from "./lib/ErrorBoundary";
// Drawer и «Система» тянут ECharts — грузим по первому открытию, первый экран легче.
const VehicleDrawer = lazy(() => import("./features/vehicle/VehicleDrawer").then((m) => ({ default: m.VehicleDrawer })));
const SystemPanel = lazy(() => import("./features/system/SystemPanel").then((m) => ({ default: m.SystemPanel })));

function PanelFailed({ onClose }: { onClose: () => void }) {
  return (
    <div className="banner banner-offline panel-failed" role="alert">
      <strong>Панель не открылась.</strong>
      <span>Пульт работает, попробуйте открыть ещё раз.</span>
      <button className="whatif-btn" onClick={onClose}>Закрыть</button>
    </div>
  );
}

export default function App() {
  useEffect(() => startStream(), []);
  const selectedId = useUi((s) => s.selectedIncidentId);
  const vehicleId = useUi((s) => s.selectedVehicleId);
  const systemOpen = useUi((s) => s.systemOpen);

  // Esc закрывает верхний слой: панель «Система», затем drawer ТС, затем карточку.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const ui = useUi.getState();
      if (ui.systemOpen) ui.setSystemOpen(false);
      else if (ui.selectedVehicleId) ui.selectVehicle(null);
      else ui.selectIncident(null);
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
          {vehicleId && (
            <ErrorBoundary key={vehicleId} fallback={<PanelFailed onClose={() => useUi.getState().selectVehicle(null)} />}>
              <Suspense fallback={null}>
                <VehicleDrawer id={vehicleId} />
              </Suspense>
            </ErrorBoundary>
          )}
        </div>
        <aside className="side">
          {selectedId ? <IncidentCard key={selectedId} id={selectedId} /> : <IncidentFeed />}
        </aside>
      </main>
      {systemOpen && (
        <ErrorBoundary fallback={<PanelFailed onClose={() => useUi.getState().setSystemOpen(false)} />}>
          <Suspense fallback={null}>
            <SystemPanel />
          </Suspense>
        </ErrorBoundary>
      )}
    </div>
  );
}
