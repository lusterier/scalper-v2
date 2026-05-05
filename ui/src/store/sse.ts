// T-413 — Zustand SSE connection-state store. FIRST Zustand store in
// `ui/`. Per BRIEF §3.4:315 + §14.1:2047 (Zustand mandate for UI client
// state); explicit §N6 exception — UI client-side state singleton is
// React idiom, not a backend service-instance global.
//
// Owns ONLY the EventSource lifecycle state shared across consumers
// (status / lastEventAt / subscriberCount). Per WG#2: bot-selection
// state lives in a SEPARATE store (`useNavStore`) — connection
// lifecycle vs nav selection are different concerns.

import { create } from "zustand";

export type SSEStatus = "unknown" | "connecting" | "connected" | "disconnected";

interface SSEStoreState {
  status: SSEStatus;
  lastEventAt: number | null;
  subscriberCount: number;
  setStatus: (s: SSEStatus) => void;
  recordEvent: () => void;
  incrementSubscribers: () => void;
  decrementSubscribers: () => void;
}

export const useSSEStore = create<SSEStoreState>((set) => ({
  status: "unknown",
  lastEventAt: null,
  subscriberCount: 0,
  setStatus: (status) => set({ status }),
  recordEvent: () => set({ lastEventAt: Date.now() }),
  incrementSubscribers: () => set((s) => ({ subscriberCount: s.subscriberCount + 1 })),
  decrementSubscribers: () =>
    set((s) => ({ subscriberCount: Math.max(0, s.subscriberCount - 1) })),
}));
