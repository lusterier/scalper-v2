// T-422 — Scenario 1: Overview renders 5 tiles + top-bar.
// Verifies T-412 deliverable surface per BRIEF §14.3:2060.

import { expect, test } from "@playwright/test";

import { mockApiRoutes } from "./fixtures/api-mocks";

test("Overview renders 5 tiles + top-bar elements", async ({ page }) => {
  await mockApiRoutes(page);
  await page.goto("/");

  // Per BRIEF §14.3:2060 — 5 tiles in cross-bot dashboard.
  await expect(page.getByText("Open positions").first()).toBeVisible();
  await expect(page.getByText("Virtual balance")).toBeVisible();
  await expect(page.getByText("24h P&L")).toBeVisible();
  await expect(page.getByText("Signals (24h)")).toBeVisible();
  await expect(page.getByText("Alerts (24h)")).toBeVisible();

  // Top bar — BotSelector + TimeRangePicker presets + ConnectionDot
  await expect(page.getByTestId("connection-dot")).toBeVisible();
  await expect(page.getByRole("button", { name: "24h" })).toBeVisible();
  await expect(page.getByRole("button", { name: "7d" })).toBeVisible();
  await expect(page.getByRole("button", { name: "30d" })).toBeVisible();
});
