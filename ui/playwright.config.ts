// T-422 — Playwright config per BRIEF §14.6:2089 + §19:2571.
// Per OQ-5=A — chromium only (single-browser smoke; multi-browser
// deferred F5+).
//
// webServer config auto-spawns Vite dev server before tests run.
// Per OQ-2=B — `/api/*` requests intercepted by mockApiRoutes() helper
// in fixtures/api-mocks.ts; no real backend connection attempted.

import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env["CI"],
  retries: process.env["CI"] ? 2 : 0,
  // Per WG#1 — workers=1 in CI is deterministic-by-design (avoids flake
  // from shared Vite dev server + browser process contention). Local
  // default = CPU-count parallel for fast dev loop. Per §N9 — implicit-
  // default choice gets explicit rationale so future maintainers don't
  // "fix" the CI=1 setting.
  workers: process.env["CI"] ? 1 : undefined,
  reporter: process.env["CI"] ? "github" : "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env["CI"],
    timeout: 120_000,
  },
});
