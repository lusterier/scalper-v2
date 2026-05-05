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

// T-412 — mirror of services/analytics_api/app/models/positions.py
// (OpenPositionResponse). NUMERIC columns serialised as JSON string
// per §5.3 to preserve Decimal precision; T-412 OverviewPage only
// reads `.length` of positions array, but downstream T-413 will
// consume row fields (entry_price, running_pnl etc.) verbatim.
export interface OpenPosition {
  bot_id: string;
  symbol: string;
  trade_id: number;
  side: string;
  entry_price: string;
  qty: string;
  remaining_qty: string;
  sl_price: string | null;
  tp_price: string | null;
  sl_type: string | null;
  best_price: string | null;
  tp_hit: boolean;
  trailing_active: boolean;
  running_pnl: string;
  mfe_price: string | null;
  mae_price: string | null;
  updated_at: string;
}

export interface OpenPositionListResponse {
  positions: OpenPosition[];
}

// T-412 — mirror of services/analytics_api/app/models/analytics.py
// (PnlSeriesPointResponse + PnlSeriesResponse). bucket_pnl /
// cumulative_pnl are §5.3 Decimal-as-string.
export interface PnlSeriesPoint {
  bucket_at: string;
  bucket_pnl: string;
  cumulative_pnl: string;
}

export interface PnlSeriesResponse {
  points: PnlSeriesPoint[];
  bot_id: string | null;
  from_at: string | null;
  to_at: string | null;
  bucket: "hour" | "day";
}

// T-412 — mirror of services/analytics_api/app/models/signals.py
// (SignalListResponse). T-412 OverviewPage only reads `.total` count
// for the 24h-window tile; full Signal row interface lands in T-413
// (per-bot live signals feed) / T-414 (trade explorer drill-down).
export interface SignalListResponse {
  signals: unknown[];
  total: number;
  limit: number;
  offset: number;
}

// T-413 — typed Signal subset for the per-bot live signals feed. Per
// WG#7: this interface OMITS 5 backend fields from SignalResponse
// (`schema_version`, `source`, `idempotency_key`, `original_symbol`,
// `payload`) — T-413 SignalFeed renders only the 6 fields below. T-414
// trade drill-down may extend with `payload` for full timeline view;
// no eager-typing speculative fields per §0.8 anti-hypothetical.
export interface Signal {
  id: number;
  received_at: string;
  symbol: string;
  action: string;
  ingestion_status: "validated" | "duplicate" | "invalid";
  correlation_id: string;
}

// Typed alternative to T-412's SignalListResponse (which uses
// `unknown[]` because Section 1 Overview only consumes `.total`). T-413
// PerBotPage actively renders signals[] so it needs the typed form.
// Both interfaces coexist; T-414 may deprecate one in favour of the
// other once consumer demand crystallises.
export interface PaginatedSignalListResponse {
  signals: Signal[];
  total: number;
  limit: number;
  offset: number;
}

// T-414 — Trade interface, exact 21-field mirror of services/
// analytics_api/app/models/trades.py:TradeResponse (verified 2026-05-05).
// NUMERIC fields → string per §5.3; DOUBLE PRECISION → number per §5.13;
// JSONB → Record<string, unknown>; datetime → ISO-8601 string.
export interface Trade {
  id: number;
  bot_id: string;
  signal_id: number | null;
  open_order_id: number;
  close_order_id: number | null;
  symbol: string;
  side: string;
  entry_price: string;
  exit_price: string | null;
  qty: string;
  notional_usd: string;
  realized_pnl: string | null;
  fees_paid: string | null;
  close_reason: string | null;
  opened_at: string;
  closed_at: string | null;
  status: "open" | "closed" | "error";
  mfe_pct: number | null;
  mae_pct: number | null;
  confidence_score: number | null;
  meta: Record<string, unknown>;
}

export interface TradeListResponse {
  trades: Trade[];
  total: number;
  limit: number;
  offset: number;
}

// T-414 — full SignalDetail mirror of SignalResponse (11 fields). T-413
// `Signal` is the 6-field SignalFeed subset; T-414 drill-down wants the
// 5 OMITTED fields (schema_version + source + idempotency_key +
// original_symbol + payload).
export interface SignalDetail {
  id: number;
  received_at: string;
  schema_version: string;
  source: string;
  idempotency_key: string;
  symbol: string;
  original_symbol: string | null;
  action: string;
  payload: Record<string, unknown>;
  ingestion_status: "validated" | "duplicate" | "invalid";
  correlation_id: string;
}

// T-414 — ScoringRuleResult mirrors packages/scoring/types.py:RuleResult
// exactly. `result` is loose `str` per backend (evaluator.py:286
// `result=str(outcome)`); possible values: "True" / "False" / "n/a" /
// "skipped" / "error_skipped" / "data_missing" / "data_stale". `error`
// is JSONB dict (e.g. `{"error": "<repr exc>"}`) or null. Plan WG#1.
export interface ScoringRuleResult {
  name: string;
  weight: number;
  applied_weight: number;
  result: string;
  error: Record<string, unknown> | null;
}

export interface ScoringEvaluation {
  id: number;
  bot_id: string;
  signal_id: number;
  evaluated_at: string;
  trigger_threshold: number;
  total_score: number;
  decision: "execute" | "reject" | "passthrough";
  config_version: number;
  rule_results: ScoringRuleResult[];
  feature_snapshot: Record<string, unknown>;
  correlation_id: string;
}

export interface ScoringEvaluationListResponse {
  evaluations: ScoringEvaluation[];
}
