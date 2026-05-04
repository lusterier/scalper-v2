// T-411 — TS mirror of analytics-api response shapes (per WG#1).
//
// Hand-maintained mirror of Pydantic models in services/analytics_api/app/
// models/. T-411 ships only Bot + BotListResponse for BotSelector consumer.
// T-412..T-420 extend per consumer endpoint (Trade / Signal / Position
// shapes). F5+ may auto-generate via openapi-typescript from FastAPI
// OpenAPI schema if drift exceeds 5 incidents (per §0.8 deferral).

// Per WG#1 — exact 8-field match with services/analytics_api/app/models/
// bots.py:BotResponse (verified 2026-05-04). Pydantic serializes datetime
// to ISO-8601 string in JSON; Decimal would serialize to string too (none
// in Bot, but pattern is shared with future Trade).
export interface Bot {
  bot_id: string;
  display_name: string;
  created_at: string; // ISO-8601 from Pydantic datetime
  status: "active" | "paused" | "archived";
  exchange_mode: "live" | "testnet" | "paper";
  config_hash: string;
  config_applied_at: string;
  // Per WG#1 / BLOCKER #3 — explicit Record typing for TS strict
  // noUncheckedIndexedAccess. T-411 BotSelector consumes only bot_id +
  // display_name + status; remaining fields typed for future consumers
  // (T-420 Settings) without forcing `any`.
  meta: Record<string, unknown>;
}

// Per WG#1 / BLOCKER #1 — backend BotListResponse returns ONLY {bots}.
// NO `total` field. List endpoint returns all bots (BRIEF expects <10
// rows; no pagination). Verified against Pydantic model line 40-43.
export interface BotListResponse {
  bots: Bot[];
}
