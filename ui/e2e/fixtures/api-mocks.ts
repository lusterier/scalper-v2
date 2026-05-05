// T-422 — Centralized fixtures for /api/* mock responses. Per OQ-2=B
// — Playwright page.route() intercepts BEFORE Vite proxy attempts to
// reach 127.0.0.1:8000; tests run safely without backend.
//
// Fixtures mirror api-types.ts shapes (kept in lockstep — when backend
// schema changes, both files update together). T-422 ships minimum
// fixture surface for 3 critical-path scenarios.

import type { Page } from "@playwright/test";

const BOT = {
  bot_id: "alpha",
  display_name: "Alpha Bot",
  created_at: "2026-05-04T00:00:00Z",
  status: "active",
  exchange_mode: "paper",
  config_hash: "deadbeefcafebabe1234567890",
  config_applied_at: "2026-05-05T10:00:00Z",
  meta: {},
};

const TRADE = {
  id: 7,
  bot_id: "alpha",
  signal_id: 100,
  open_order_id: 10,
  close_order_id: 11,
  symbol: "BTCUSDT",
  side: "long",
  entry_price: "50000.00",
  exit_price: "51000.00",
  qty: "0.1",
  notional_usd: "5000.00",
  realized_pnl: "100.00",
  fees_paid: "5.00",
  close_reason: "tp",
  opened_at: "2026-05-05T10:00:00Z",
  closed_at: "2026-05-05T12:00:00Z",
  status: "closed",
  mfe_pct: 0.025,
  mae_pct: -0.005,
  confidence_score: 0.78,
  meta: {},
};

const SIGNAL_DETAIL = {
  id: 100,
  received_at: "2026-05-05T09:59:30Z",
  schema_version: "1.0",
  source: "tv",
  idempotency_key: "abc-123",
  symbol: "BTCUSDT",
  original_symbol: null,
  action: "LONG",
  payload: {},
  ingestion_status: "validated",
  correlation_id: "corr-12345678",
};

const SCORING_EVALUATION = {
  id: 200,
  bot_id: "alpha",
  signal_id: 100,
  evaluated_at: "2026-05-05T09:59:31Z",
  trigger_threshold: 0.5,
  total_score: 0.7,
  decision: "execute",
  config_version: 3,
  rule_results: [
    { name: "rsi_below_30", weight: 0.3, applied_weight: 0.3, result: "True", error: null },
  ],
  feature_snapshot: { "ind.btcusdt.15m.rsi_14": 42.5 },
  correlation_id: "corr-12345678",
};

export async function mockApiRoutes(page: Page): Promise<void> {
  // Bots — used by BotSelector + Trade explorer + Settings.
  await page.route("**/api/bots/", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ bots: [BOT] }) }),
  );

  // Positions — Overview tile + Per-bot panel.
  await page.route("**/api/positions/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        positions: [
          {
            bot_id: "alpha",
            symbol: "BTCUSDT",
            trade_id: 1,
            side: "long",
            entry_price: "50000.00",
            qty: "0.1",
            remaining_qty: "0.1",
            sl_price: "49000.00",
            tp_price: "52000.00",
            sl_type: "protective",
            best_price: "50500.00",
            tp_hit: false,
            trailing_active: false,
            running_pnl: "50.00",
            mfe_price: "50800.00",
            mae_price: "49500.00",
            updated_at: "2026-05-05T11:00:00Z",
          },
        ],
      }),
    }),
  );

  // Signals — Overview signal counts + Per-bot feed + Scoring inspector list.
  await page.route("**/api/signals/?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        signals: [
          {
            id: 100,
            received_at: "2026-05-05T09:59:30Z",
            symbol: "BTCUSDT",
            action: "LONG",
            ingestion_status: "validated",
            correlation_id: "corr-12345678",
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      }),
    }),
  );

  // Signal detail — Trade drill-down.
  await page.route("**/api/signals/100", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SIGNAL_DETAIL) }),
  );

  // Analytics pnl-series — Overview + Per-bot chart.
  await page.route("**/api/analytics/pnl-series**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        points: [
          { bucket_at: "2026-05-05T10:00:00Z", bucket_pnl: "10.00", cumulative_pnl: "10.00" },
          { bucket_at: "2026-05-05T11:00:00Z", bucket_pnl: "20.00", cumulative_pnl: "30.00" },
        ],
        bot_id: null,
        from_at: null,
        to_at: null,
        bucket: "hour",
      }),
    }),
  );

  // Trades list + detail.
  await page.route("**/api/trades/?**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ trades: [TRADE], total: 1, limit: 50, offset: 0 }),
    }),
  );
  await page.route("**/api/trades/7", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TRADE) }),
  );

  // Scoring by signal — Trade drill-down.
  await page.route("**/api/scoring/by-signal/100", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ evaluations: [SCORING_EVALUATION] }),
    }),
  );
}
