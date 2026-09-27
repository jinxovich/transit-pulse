import { create } from "zustand";

/** Выбор диспетчера: открытая карточка инцидента, drawer ТС и панель «Система». */
interface UiState {
    selectedIncidentId: string | null;
    selectedVehicleId: string | null;
    systemOpen: boolean;
    selectIncident: (id: string | null) => void;
    selectVehicle: (id: string | null) => void;
    setSystemOpen: (open: boolean) => void;
}

export const useUi = create<UiState>((set) => ({
    selectedIncidentId: null,
    selectedVehicleId: null,
    systemOpen: false,
    selectIncident: (id) => set({ selectedIncidentId: id }),
    selectVehicle: (id) => set({ selectedVehicleId: id }),
    setSystemOpen: (systemOpen) => set({ systemOpen }),
}));
