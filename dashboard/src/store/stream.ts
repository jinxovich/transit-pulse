import { create } from "zustand";
import type { Incident, Kpis, SystemStatus, VehicleState, WsSnapshot } from "@contract";

interface StreamState {
    sessionId: string | null;
    simTime: string | null;
    /** performance.now() прихода последнего sim_time — якорь для плавных часов. */
    simWall: number;
    connected: boolean;
    vehicles: Record<string, VehicleState>;
    incidents: Record<string, Incident>;
    kpis: Kpis | null;
    status: SystemStatus | null;

    touch: (simTime: string) => void;
    setConnected: (v: boolean) => void;
    replaceAll: (msg: WsSnapshot) => void;
    upsertVehicles: (list: VehicleState[], removed: string[]) => void;
    upsertIncident: (inc: Incident) => void;
    setKpis: (k: Kpis) => void;
    setStatus: (s: SystemStatus) => void;
}

export const useStream = create<StreamState>((set) => ({
    sessionId: null,
    simTime: null,
    simWall: 0,
    connected: false,
    vehicles: {},
    incidents: {},
    kpis: null,
    status: null,

    touch: (simTime) => set({ simTime, simWall: performance.now() }),
    setConnected: (connected) => set({ connected }),
    replaceAll: (msg) =>
    set({
        sessionId: msg.session_id,
        simTime: msg.sim_time,
        simWall: performance.now(),
        vehicles: Object.fromEntries(msg.data.vehicles.map((v) => [v.vehicle_id, v])),
        incidents: Object.fromEntries(msg.data.incidents.map((i) => [i.id, i])),
        kpis: msg.data.kpis,
        status: msg.data.status,
    }),
    upsertVehicles: (list, removed) =>
    set((s) => {
        const vehicles = { ...s.vehicles };
        for (const v of list) vehicles[v.vehicle_id] = v;
        for (const id of removed) delete vehicles[id];
        return { vehicles };
    }),
    upsertIncident: (inc) => set((s) => ({ incidents: { ...s.incidents, [inc.id]: inc } })),
    setKpis: (kpis) => set({ kpis }),
    setStatus: (status) => set({ status }),
}));