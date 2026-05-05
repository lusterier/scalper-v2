// T-413 — Zustand navigation store. Owns the "last bot the operator
// selected" so the left-nav per-bot link can resolve $botId without
// a per-route prop drill. Per WG#2: kept SEPARATE from `useSSEStore`
// because connection lifecycle and nav selection are different
// concerns; merging would obscure store ownership later (T-414+
// trade explorer / T-415 backtest lab will likely add their own
// last-selected state).
//
// In-memory only — F5+ may persist to localStorage; per §0.8 anti-
// hypothetical, no persistence in F4.

import { create } from "zustand";

interface NavStoreState {
  lastSelectedBotId: string | null;
  setLastSelectedBotId: (botId: string | null) => void;
}

export const useNavStore = create<NavStoreState>((set) => ({
  lastSelectedBotId: null,
  setLastSelectedBotId: (lastSelectedBotId) => set({ lastSelectedBotId }),
}));
