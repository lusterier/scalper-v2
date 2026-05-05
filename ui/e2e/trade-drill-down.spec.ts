// T-422 — Scenario 3: Trade explorer drill-down. Verifies T-414
// 8-section drill-down + 5 placeholder rendering per BRIEF §14.3:2062.

import { expect, test } from "@playwright/test";

import { mockApiRoutes } from "./fixtures/api-mocks";

test("Trade explorer: list → row click → drill-down 8 sections", async ({ page }) => {
  await mockApiRoutes(page);
  await page.goto("/trades");

  // List renders DataTable with at least 1 trade row from fixture.
  await expect(page.getByText("BTCUSDT").first()).toBeVisible();

  // Click row → navigates to /trades/$tradeId. Use trade_id=7 from
  // fixture (TRADE constant in api-mocks.ts).
  await page.getByText("BTCUSDT").first().click();
  await expect(page).toHaveURL(/\/trades\/7/);

  // 8 sections per BRIEF §14.3:2062 = 1 trade summary header + 7 BRIEF
  // tiers (signal + scoring + 5 placeholders).
  await expect(page.getByText("Trade #7")).toBeVisible();
  await expect(page.getByText("Trade summary")).toBeVisible();
  await expect(page.getByText("Signal details")).toBeVisible();
  await expect(page.getByText("Scoring breakdown")).toBeVisible();
  await expect(page.getByText("Order events")).toBeVisible();
  await expect(page.getByText("Fills")).toBeVisible();
  await expect(page.getByText("SL moves")).toBeVisible();
  await expect(page.getByText("Shadow variants")).toBeVisible();
  await expect(page.getByText("Post-close price snapshots")).toBeVisible();

  // Verify at least 1 placeholder explicitly shows "Coming F4+" / "F5+".
  await expect(page.getByText(/Coming F[45]\+/).first()).toBeVisible();

  // Back link present.
  await expect(page.getByRole("link", { name: "Back to Trades" })).toBeVisible();
});
