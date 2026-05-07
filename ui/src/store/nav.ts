// T-413 — Zustand navigation store. Owns the "last bot the operator
// selected" so the left-nav per-bot link can resolve $botId without
// a per-route prop drill. Per WG#2: kept SEPARATE from `useSSEStore`
// because connection lifecycle and nav selection are different
// concerns; merging would obscure store ownership later (T-414+
// trade explorer / T-415 backtest lab will likely add their own
// last-selected state).
//
// T-520 cherry-pick (2026-05-07): added `persist` middleware over
// localStorage. Resolves F4 E1 smoke nit where per-bot + strategy-
// editor left-nav links stayed disabled after page refresh until the
// operator re-picked a bot in Overview. `partialize` keeps only
// `lastSelectedBotId` in localStorage; setter is rebuilt by Zustand
// at hydration, not persisted. `version: 1` so future schema bumps
// can drop stale state cleanly.

import { create } from "zustand";
import { persist } from "zustand/middleware";

interface NavStoreState {
  lastSelectedBotId: string | null;
  setLastSelectedBotId: (botId: string | null) => void;
}

export const useNavStore = create<NavStoreState>()(
  persist(
    (set) => ({
      lastSelectedBotId: null,
      setLastSelectedBotId: (lastSelectedBotId) => set({ lastSelectedBotId }),
    }),
    {
      name: "scalper-v2-nav",
      version: 1,
      partialize: (state) => ({ lastSelectedBotId: state.lastSelectedBotId }),
    }
  )
);
