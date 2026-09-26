import { create } from "zustand";

/** что выбрал диспетчер   */
interface UiState {
    selectedIncidentId: string | null;
    selectIncident: (id: string | null) => void;
}

export const useUi = create<UiState>((set) => ({
    selectedIncidentId: null,
    selectIncident: (id) => set({ selectedIncidentId: id }),
}));