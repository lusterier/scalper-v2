// T-422 — Scenario 2: Per-bot navigation. Verifies T-411 BotSelector +
// T-413 Per-bot live view integration per BRIEF §14.3:2061.

import { expect, test } from "@playwright/test";

import { mockApiRoutes } from "./fixtures/api-mocks";

test("Per-bot navigation: /bot/alpha route renders 3 panels", async ({ page }) => {
  await mockApiRoutes(page);

  // Direct URL navigation — /bot/$botId is deep-linkable per OQ-1=A.
  // Tests the route surface; BotSelector-driven navigation has its own
  // unit tests in vitest (BotSelector.test.tsx + PerBot.test.tsx).
  await page.goto("/bot/alpha");

  // 3 panels per BRIEF §14.3:2061 — open positions table + live signals
  // feed + cumulative P&L chart.
  await expect(page.getByText("Open positions")).toBeVisible();
  await expect(page.getByText("Live signals")).toBeVisible();
  await expect(page.getByText("Cumulative P&L (24h)")).toBeVisible();
});
