# ADR-0013: §B.1 tier-sizing computes in the execution-service placement seam (not the strategy-engine risk-gate location)

**Status:** Accepted (2026-05-16, T-527b plan-stage Gate-1 pass-2 APPROVE; operator review per §6.7 — accepted as drafted 2026-05-16)
**Context window:** F5 ADR-0011 position-sizing cluster (T-527a config foundation shipped → T-527b the sizing compute)
**Authors:** Operator (T-527b plan-stage OQ-1/2/3 decisions 2026-05-16, all recommended option; §6.7 ADR acceptance 2026-05-16) + Claude Code (Explore code-path analysis surfacing the adapter-only-inputs constraint) + plan-reviewer (Gate-1 pass-2 APPROVE — ADR adjudicated sound)

## Decision

The BRIEF §B.1 tier-ladder position sizing (reified by T-527: balance→tier→`score_multiplier`→`max_notional_per_symbol` cap→`qty = notional ÷ reference_price`) **computes in the execution-service placement seam** (`services/execution/app/placement.py` `_handle`, immediately before the existing T-529 `quantize_qty` pre-flight) — **NOT** in a strategy-engine pre-publish gate alongside the T-524 concurrent-caps / T-526 cooldown risk gates.

Concretely:

1. **Inputs are adapter-sourced in execution-service**: account balance via `adapter.get_account_balance(sub_account).total_equity` (T-530, shipped); pre-trade reference price via a **NEW `ExchangeClient.get_mark_price(symbol) -> Decimal` protocol method** (T-527b1 — Bybit `GET /v5/market/tickers`, paper stub; no pre-trade price method exists on the protocol today).
2. **`SizingSection` threaded onto `OrderRequest`**: the strategy-engine producer maps `BotConfig.sizing` → an additive `OrderRequest` field (single-source from `BotConfig`, producer-mapped — the SAME pattern as T-527a `score` and T-511b2 `shadow_variants`). Execution-service does **not** load YAML / bot config.
3. **Sub-lowest-tier balance → skip before `place_market_order`**: when `total_equity` < the lowest tier `balance_min`, the signal is skipped (no order placed; structured `trading.log` event + Prometheus counter) — a **NEW execution-service pre-place skip shape**, deliberately diverging from the strategy-engine pre-publish gate-location of T-524/T-526.
4. **L-007 split** T-527b → **T-527b1** (`get_mark_price` adapter-protocol extension; no consumer; T-530/T-534a-shaped foundation leaf) + **T-527b2** (`packages/sizing` pure compute module + the `SizingSection`→qty wiring + skip path + producer/placement plumbing; blocked-by T-527b1; §N4 financial-math TDD + Gate-4 math-validator + `## Hand verification`).
5. **score→multiplier mapping** (T-527b2): `key = str(floor(score))` clamped into the `[min,max]` of the present `score_multipliers` keys; `score is None` or no usable key → multiplier ×1.0 (notional = `tier.size` unmultiplied). Deterministic; never blocks a scored signal on a sizing-config gap.

This is a deliberate, scoped divergence from the established v2 risk-gate architecture (T-524/T-526 run in strategy-engine pre-publish, DB-derived). It is recorded as an ADR because BRIEF §B.1 is silent on both the compute-placement service and the reference-price source — a brief gap requiring a cross-service architecture decision.

## Context

### BRIEF §B.1 verbatim scope + gap

BRIEF §22 Appendix B `configs/bots/alpha.yaml` (lines 3130-3145) shows the `sizing:` block — `tiers` (balance→size USD ladder), `score_multipliers`, `max_notional_per_symbol` — but the BRIEF specifies neither **which service computes the size** nor **what reference price** converts notional USD → order qty. T-310a deliberately deferred §B.1 to a fixed `execution.qty`; ADR-0011 reified it as the T-527 hardening task; T-527 plan-stage OQ-3=A fixed "`size`/`max_notional` = notional USD; `qty = notional ÷ latest mark/ticker price via the adapter". The remaining gap — *which service, fed from where* — is this ADR.

### The established v2 risk-gate architecture (T-524 / T-526)

T-524 (concurrent-trades caps) and T-526 (cooldown after loss) both run as **strategy-engine pre-publish gates** in `consumer.py` (before scoring publish), deriving state **purely from the trades DB** (`packages/db/queries/trades.py` `count_open_trades` / `select_recent_closed_trades`) with **no exchange adapter**. The silent-skip pattern is uniform: `if gate.blocked: trading_logger.info("signal_blocked_<x>"); metrics.signals_blocked_<x>.inc(); return` — skip happens BEFORE the OrderRequest is published. strategy-engine has no exchange adapter by design (separation of concerns: scoring vs execution I/O).

### Why §B.1 sizing structurally cannot follow that pattern

§B.1 sizing needs two inputs that are **adapter-only**:

- **Account balance** (`AccountBalance.total_equity`) — produced by `ExchangeClient.get_account_balance(sub_account)` (T-530). The only DB-resident alternative is T-531's `bot_equity_snapshots` hypertable, but T-531 shipped **writer-only** (`insert_equity_snapshot`; "no select helper — reader is a later task"), and even a new read helper yields a ≤5-min-stale snapshot, not the live balance.
- **A pre-trade reference price** (to turn notional USD → order qty for a market order, which needs `qty` *before* placement). `ExchangeClient` exposes **no** pre-trade price method (`get_fill_price` is post-fill only; `get_instrument_info` returns qty-step/min-notional, no price). The scoring feature-store carries only the bot's configured indicators (ema/rsi/…), not a guaranteed current price; the `SignalValidated` envelope does not carry price. A live price is reachable **only** via the exchange adapter (Bybit `/v5/market/tickers`).

The exchange adapter exists **only in execution-service** (`app.state.adapters: dict[BotId, ExchangeClient]`; bound per-bot into the `placement.py` handler). The placement handler already holds the adapter and already performs pre-trade adapter I/O (`get_instrument_info` + `quantize_qty`). It is the **only seam where both sizing inputs are simultaneously available**.

### Design choices considered (operator OQ-1, 2026-05-16)

- **A. Execution-service placement seam (CHOSEN)** — compute at `placement.py` before `quantize_qty`; balance + new `get_mark_price` via the in-scope adapter; `SizingSection` threaded onto `OrderRequest` (producer-mapped, mirror T-527a `score`); sub-lowest-tier → skip before `place_market_order`. Self-consistent (both adapter-only inputs reachable here); diverges from the T-524/T-526 gate-location but is the only place both inputs exist.
- **B. strategy-engine pre-publish gate** — architecturally consistent with T-524/T-526 (skip before publish, DB-derived balance via a new `select_latest_equity_snapshot` of T-531's table). Rejected: the notional→qty reference price has **no clean strategy-engine source** — would require either giving strategy-engine an exchange adapter (heavy coupling, breaks the scoring/execution separation) or a speculative price-feature / signal-price plumbing the BRIEF does not define.
- **C. Hybrid (eligibility/skip in strategy-engine from DB balance; notional→qty in execution-service)** — rejected: most moving parts, cross-service sizing state, two-stage flow; no capital-safety gain over A (A's skip is already pre-place, no order placed → no capital at risk).

Operator chose **A** (OQ-1=A 2026-05-16). The price-source gap (no pre-trade price method) is closed by the new `get_mark_price` protocol method (T-527b1); OQ-3=A (T-527 plan-stage) had already committed "via the adapter".

## Consequences

### Accepted

- **§B.1 sizing is execution-service-resident.** The tier-ladder math lives in a NEW pure `packages/sizing/` module (unit-testable, no I/O — §N4 TDD); the orchestration (balance fetch, mark-price fetch, skip) lives in the `placement.py` seam (T-527b2).
- **`SizingSection` is threaded onto `OrderRequest`** (additive field, default `None`, `schema_version` stays `"1.0"` — verbatim the T-527a `score` / T-511b2 `shadow_variants` additive-field precedent). Single source = `BotConfig.sizing`; the strategy-engine producer maps it (mirror T-527a `score=result.total_score`). Execution-service stays free of YAML/bot-config loading — it consumes the wire envelope only (preserves the §2.5/§6.4 "execution-service is a stateless wire handler" posture; this is config-data ON the wire, not config-loading IN execution-service).
- **NEW `ExchangeClient.get_mark_price(symbol) -> Decimal` protocol method** (T-527b1): Bybit `GET /v5/market/tickers?category=linear&symbol=…` (public, unauth, rate-limited via the existing limiter); paper adapter returns a deterministic stub (last fill / fixed). T-530/T-534a-shaped protocol extension with **no consumer** (T-527b2 is first consumer).
- **Pre-place skip = no capital at risk.** Sub-lowest-tier balance → skip BEFORE `place_market_order`; no order is ever placed. Structured `trading.log` event (`execution.signal_skipped_sizing` proposed) + Prometheus counter on the execution-service registry (T-531 shipped `build_execution_metrics` — the scaffold exists).
- **Cross-cutting consistency**: T-527b2 sizing compute + the skip + the `OrderRequest.sizing` field bind together under this ADR; T-528 (risk-per-SL alternative, `sizing.method` discriminator) will inherit the same execution-service-resident, adapter-fed shape.

### Trade-offs

- **Divergence from the T-524/T-526 gate-location.** A maintainer must know that sizing-skip is execution-side (post-publish, pre-place), unlike the strategy-engine pre-scoring caps/cooldown gates. This ADR is the single source recording *why* (adapter-only inputs). The risk gates are NOT moved; only sizing differs, for a structural reason.
- **Skip is post-scoring / post-publish.** A scored signal + a published `OrderRequest` can result in no order (balance < lowest tier). Accepted: that balance state is degenerate/rare (operator account below the minimum configured tier); the log + counter fire; no capital is at risk (pre-place). Not worth a second cross-service gate to skip ~one scoring cycle earlier.
- **Extra adapter RPCs per placement** — `get_account_balance` + `get_mark_price` added to the placement hot path (both `@idempotent`/retry-safe; placement already performs `get_instrument_info` + `place_market_order` network I/O; one is `category=linear` public). Acceptable per-signal cost; not cached in T-527b (a future equity-snapshot-read optimization is explicitly out of scope, flagged forward).
- **`OrderRequest` wire growth** — one additive `sizing` field (and the existing `score`). Additive, defaulted, `schema_version` unchanged; old payloads validate (no `extra="forbid"` on `OrderRequest`). Same precedent as T-511b2 / T-527a.
- **New exchange-API surface** — `get_mark_price` adds the Bybit tickers endpoint. Public/unauth, rate-limited via the existing limiter bucket; isolated in T-527b1 with its own tests; paper stub keeps backtest/paper deterministic.

### Rejected alternatives

- **B — strategy-engine pre-publish gate** (DB balance via new `select_latest_equity_snapshot`): rejected because the notional→qty reference price has no clean non-adapter source in strategy-engine; solving it would either couple an exchange adapter into the scoring service (violates the scoring/execution separation that T-524/T-526 preserve) or invent a price-feature/signal-price contract the BRIEF does not define. The architectural-consistency benefit does not outweigh inventing an unspecified price pipeline.
- **C — hybrid two-stage**: rejected — maximal moving parts + cross-service sizing state for no capital-safety gain over A (A skips pre-place).
- **Post-fill `get_fill_price` as the reference price**: rejected — logically impossible. A market order's `qty` must be decided BEFORE the order is placed; you cannot derive an order's size from the fill price of that same order.
- **Thread a pre-computed `qty` from strategy-engine** (parallel to T-527a `score`): rejected — strategy-engine cannot compute it without the balance + price it structurally lacks; threading `SizingSection` (config data) and computing in execution-service (where the inputs live) is the minimal correct plumbing.
- **No split (single T-527b)**: rejected — new protocol method + Bybit/paper impls + pure sizing module + wiring + §N4 TDD trips the §0.3 cap; pre-emptive L-007 split (b1 protocol-ext / b2 compute) per the pre-flag (mirror T-534→{a,b}).

## Implementation references

- **Builds on**: T-527a (shipped — `SizingSection`/`SizingTier` `packages/scoring/types.py`, `OrderRequest.score` additive precedent, strategy-engine producer-maps-BotConfig pattern); T-530 (shipped — `get_account_balance`/`AccountBalance.total_equity`); T-529 (shipped — `quantize_qty` + the `placement.py` pre-flight seam); T-531 (shipped — `build_execution_metrics` execution-service Prom scaffold).
- **Split-record**: `docs/plans/T-527.md` (T-527→{a,b}); recursive-split-record `docs/plans/T-527b.md` (T-527b→{b1,b2}); active leaf plan `docs/plans/T-527b1.md`.
- **Pattern precedents**: ADR-0010 (thread a config-derived discriminator onto the wire envelope, single-source from `BotConfig`, producer-mapped — directly analogous to threading `SizingSection` onto `OrderRequest`); T-534a / T-530 (adapter-protocol-extension foundation leaf with no consumer — the T-527b1 shape).
- **Does NOT supersede**: ADR-0011 (defines *what* T-527 is; ADR-0013 defines the cross-service *how*). The T-524/T-526 strategy-engine DB-derived pre-publish risk gates are unchanged — ADR-0013 explains why §B.1 sizing alone is execution-resident.

## Supersedes / superseded by

Supersedes nothing. Complements ADR-0011 (F5 hardening scope). No superseding ADR.
