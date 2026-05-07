// T-520 cherry-pick — vitest tests for zustand persist on useNavStore.
//
// Covers: localStorage roundtrip + partialize correctness + clear behavior.
// Mock-free: jsdom provides localStorage; Zustand persist middleware writes
// directly. Each test resets the store + storage before run.

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useNavStore } from "@/store/nav";

const STORAGE_KEY = "scalper-v2-nav";

describe("useNavStore — T-520 persist middleware", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useNavStore.setState({ lastSelectedBotId: null });
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("setting lastSelectedBotId writes to localStorage under scalper-v2-nav key", () => {
    useNavStore.getState().setLastSelectedBotId("alpha");
    const raw = window.localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.state.lastSelectedBotId).toBe("alpha");
    // Version 1 per partialize config.
    expect(parsed.version).toBe(1);
  });

  it("partialize: setter function is NOT persisted (only lastSelectedBotId)", () => {
    useNavStore.getState().setLastSelectedBotId("beta");
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = JSON.parse(raw!);
    // partialize whitelist: only lastSelectedBotId; setter excluded.
    expect(Object.keys(parsed.state)).toEqual(["lastSelectedBotId"]);
  });

  it("clearing lastSelectedBotId to null persists null", () => {
    useNavStore.getState().setLastSelectedBotId("gamma");
    useNavStore.getState().setLastSelectedBotId(null);
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = JSON.parse(raw!);
    expect(parsed.state.lastSelectedBotId).toBeNull();
  });

  it("hydration roundtrip: pre-populate localStorage + import store → state restored", async () => {
    // Simulate prior session state.
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ state: { lastSelectedBotId: "delta" }, version: 1 })
    );
    // Re-import store fresh (Zustand persist re-hydrates on subscribe / first access).
    // vitest runs each test with fresh module imports per default config; rehydrate manually.
    await useNavStore.persist.rehydrate();
    expect(useNavStore.getState().lastSelectedBotId).toBe("delta");
  });
});
