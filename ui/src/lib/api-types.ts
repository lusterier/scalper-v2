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

// T-516a2 — PaperTrade interface, exact 21-field mirror of services/
// analytics_api/app/models/paper_trades.py:PaperTradeResponse (T-516a1
// shipped 2026-05-09). NUMERIC fields → string per §5.3; DOUBLE PRECISION
// → number per §5.13; JSONB → Record<string, unknown>; datetime → ISO-8601.
// Structurally identical to Trade per backend §3.1:268 paper-live symmetry
// invariant. Drift mitigation: TWO distinct interfaces (no `type PaperTrade
// = Trade` alias) so future divergence triggers TS errors at usage sites.
export interface PaperTrade {
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

export interface PaperTradeListResponse {
  paper_trades: PaperTrade[];
  total: number;
  limit: number;
  offset: number;
}

// T-516b — ShadowVariant interface, exact 15-field mirror of services/
// analytics_api/app/models/shadow_variants.py:ShadowVariantResponse
// (migration 0015 schema). NUMERIC fields → string per §5.3; DOUBLE
// PRECISION → number per §5.13; JSONB → Record<string, unknown>;
// datetime → ISO-8601 string. terminal_outcome StrEnum: 5 outcomes per
// packages/core/types.py:77 ShadowVariantTerminal. parent_kind
// discriminator per ADR-0010 routes parent_trade_id to either trades.id
// (live) or paper_trades.id (paper).
export interface ShadowVariant {
  id: number;
  parent_trade_id: number;
  bot_id: string;
  variant_name: string;
  side: string;
  entry_price: string;
  qty: string;
  created_at: string;
  terminated_at: string | null;
  terminal_outcome: "sl_hit" | "be_hit" | "tp_trail" | "tp_full" | "timeout" | null;
  realized_pnl: string | null;
  mfe_pct: number | null;
  mae_pct: number | null;
  meta: Record<string, unknown>;
  parent_kind: "live" | "paper";
}

// T-517b2 — ShadowRejected interface, exact 11-field mirror of services/
// analytics_api/app/models/shadow_rejected.py:ShadowRejectedResponse
// (T-517b1 shipped; migration 0014 schema). NUMERIC absent (rejected
// signals don't trade per BRIEF §13.5); DOUBLE PRECISION → number per
// §5.13; JSONB → Record<string, unknown>; datetime → ISO-8601 string.
// terminal_outcome StrEnum: 5 values per packages/core/types.py:102
// ShadowRejectedTerminal.
export type ShadowRejectedTerminal =
  | "would_tp"
  | "would_sl"
  | "would_be"
  | "no_trigger"
  | "shutdown_mid_replay";

export interface ShadowRejected {
  id: number;
  signal_id: number;
  bot_id: string;
  symbol: string;
  would_side: string;
  created_at: string;
  terminated_at: string | null;
  terminal_outcome: ShadowRejectedTerminal | null;
  mfe_pct: number | null;
  mae_pct: number | null;
  meta: Record<string, unknown>;
}

export interface ShadowRejectedListResponse {
  rejected: ShadowRejected[];
  total: number;
  limit: number;
  offset: number;
}

// T-517a2 — VariantAggregate interface, exact 10-field mirror of services/
// analytics_api/app/models/shadow_aggregate.py:VariantAggregateResponse
// (T-517a1 shipped; commit f6bf49a). Decimal NUMERIC fields → string per
// §5.3 (total_pnl/avg_pnl/best_pnl/worst_pnl — money sums); DOUBLE PRECISION
// → number per §5.13 (win_rate / avg_mfe_pct / avg_mae_pct — statistical
// ratios; mfe/mae may be null when all rows had None at that field).
export interface VariantAggregate {
  variant_name: string;
  n_trades: number;
  win_count: number;
  win_rate: number;
  total_pnl: string;
  avg_pnl: string;
  best_pnl: string;
  worst_pnl: string;
  avg_mfe_pct: number | null;
  avg_mae_pct: number | null;
}

// Envelope: variants sorted by `(-total_pnl, variant_name)` per backend
// `compute_variant_aggregate` at services/analytics_api/app/analytics_compute.py:389
// (DESC by total_pnl + ASC by variant_name tiebreak). First-row-is-best
// invariant pinned by T-517a1 test
// test_compute_variant_aggregate_sorted_by_total_pnl_desc_tiebreak_variant_name_asc.
// `from_at` + `to_at` echo as ISO-8601 strings (Pydantic `datetime | None`
// serialised; never `Date` object) — mirrors ShadowRejectedListResponse echo
// shape.
export interface VariantAggregateListResponse {
  symbol: string;
  variants: VariantAggregate[];
  bot_id: string | null;
  from_at: string | null;
  to_at: string | null;
}

export interface ShadowVariantListResponse {
  variants: ShadowVariant[];
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

// T-419 — AuditEvent exact 10-field mirror of services/analytics_api/
// app/models/audit.py:AuditEventResponse per WG#4. JSONB fields typed
// as Record<string, unknown> | null; meta is non-null per backend.
export interface AuditEvent {
  id: number;
  occurred_at: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  correlation_id: string | null;
  meta: Record<string, unknown>;
}

export interface AuditEventListResponse {
  events: AuditEvent[];
  total: number;
  limit: number;
  offset: number;
}

// T-420 — SymbolMapEntry exact 6-field mirror of services/analytics_api/
// app/models/symbol_map.py:SymbolMapEntryResponse. exchange_source per
// packages/core/types.py:83-92 ExchangeSource StrEnum (binance/bybit/
// custom).
export interface SymbolMapEntry {
  input_symbol: string;
  canonical_symbol: string;
  exchange_source: "binance" | "bybit" | "custom";
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface SymbolMapListResponse {
  entries: SymbolMapEntry[];
}

export interface SymbolMapEntryCreateRequest {
  input_symbol: string;
  canonical_symbol: string;
  exchange_source: "binance" | "bybit" | "custom";
  notes: string | null;
}

// PUT body excludes input_symbol per backend WG#10 (URL path is the PK).
export interface SymbolMapEntryUpdateRequest {
  canonical_symbol: string;
  exchange_source: "binance" | "bybit" | "custom";
  notes: string | null;
}
