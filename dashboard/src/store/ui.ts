import { create } from "zustand";

/** Выбор диспетчера: открытая карточка инцидента. */
interface UiState {
    selectedIncidentId: string | null;
    selectIncident: (id: string | null) => void;
}

export const useUi = create<UiState>((set) => ({
    selectedIncidentId: null,
    selectIncident: (id) => set({ selectedIncidentId: id }),
}));