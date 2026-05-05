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

// T-415 — BacktestRun mirror of services/analytics_api/app/models/
// backtests.py:BacktestRunResponse (12 fields). UUID `id` serialised as
// string. NUMERIC fields are absent in this table; all timestamps are
// ISO-8601; `summary` is JSONB (always null in F4 — F5+ worker
// populates).
export interface BacktestRun {
  id: string;
  name: string;
  bot_id: string;
  config_yaml: string;
  config_hash: string;
  date_range_start: string;
  date_range_end: string;
  status: "queued" | "running" | "completed" | "failed";
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown> | null;
  notes: string | null;
}

export interface BacktestRunListResponse {
  runs: BacktestRun[];
  total: number;
  limit: number;
  offset: number;
}

// T-415 — POST /api/backtests/ request body. Mirrors backend
// BacktestRunCreateRequest 6 fields. date_range_start / date_range_end
// MUST be ISO-8601 with .toISOString() Z-suffix per §N1.
export interface BacktestRunCreateRequest {
  name: string;
  bot_id: string;
  config_yaml: string;
  date_range_start: string;
  date_range_end: string;
  notes: string | null;
}

// T-416 — Mirrors `services/analytics_api/app/models/configs.py`
// exactly. Drift = compile-time error or 422 round-trip; manually
// reconcile when backend schema evolves. WG#2 enforces 8/4/2/4/3
// field counts.

export interface BotConfig {
  id: number;
  bot_id: string;
  version: number;
  applied_at: string;
  applied_by: string;
  config_yaml: string;
  config_hash: string;
  notes: string | null;
}

export interface BotConfigVersionsListResponse {
  versions: BotConfig[];
  total: number;
  limit: number;
  offset: number;
}

export interface ConfigValidateRequest {
  bot_id: string;
  yaml_text: string;
}

export interface ConfigValidateResponse {
  valid: boolean;
  bot_id: string;
  parsed_version: number | null;
  errors: string[];
}

export interface ConfigApplyRequest {
  yaml_text: string;
  applied_by: string;
  notes: string | null;
}

// T-417 — FeatureRow exact 7-field mirror of services/analytics_api/
// app/models/features.py:FeatureResponse. value_num is float per §5.13
// (statistical metric, NOT money — Decimal-as-string preservation does
// not apply). value_bool / value_json mutually exclusive null pattern
// (only one populated per feature definition; backend returns the other
// 2 as null). value_json Pydantic union accepts dict OR list — TS
// mirrors as discriminated union.
export interface FeatureRow {
  feature_name: string;
  symbol: string;
  computed_at: string;
  value_num: number | null;
  value_bool: boolean | null;
  value_json: Record<string, unknown> | unknown[] | null;
  source_version: string;
}

export interface FeatureLatestListResponse {
  features: FeatureRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface FeatureHistoryListResponse {
  features: FeatureRow[];
  total: number;
  limit: number;
  offset: number;
}
