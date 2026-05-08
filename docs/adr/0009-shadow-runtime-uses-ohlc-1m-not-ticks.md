# ADR-0009: Shadow runtime uses `market.ohlc.1m.>` stream, not `market.ticks.<symbol>` per BRIEF §13.3

**Status:** Accepted (2026-05-08, T-511b plan-stage Gate 1)
**Context window:** F5 shadow runtime cluster (T-511b1 + T-511b2 + T-512 + T-513)
**Authors:** Operator + plan-reviewer (cross-cutting deviation flagged in T-511b plan-reviewer pass 1)

## Decision

The F5 shadow runtime — `ShadowWorker` per-variant FSM (T-511b1/b2), OHLC replay restart-recovery (T-512), and rejected-signal observation (T-513) — consumes the existing **`market.ohlc.1m.>` NATS stream**, NOT the BRIEF §13.3 verbatim `market.ticks.<symbol>` topic.

`PaperExchange._on_candle` intra-candle path (T-213c) handles SL/TP cross detection at candle granularity. Shadow worker FSM tick = 1 OHLC candle event (1m cadence). BE-trigger / trail-SL adjustments computed on candle close.

## Context

BRIEF §13.3:1985 says verbatim:

> Variants subscribe to `market.ticks.<symbol>` (no exchange writes).

In v2, no tick stream exists. F2..F4 reality: market-data-service publishes only `market.ohlc.1m.<symbol>` (1-minute OHLCV candles via Binance WebSocket OHLC subscription). Live execution-service `lifecycle._step` runs on 1m candle cadence. PaperExchange `_on_candle` consumes the same 1m stream.

Implementing tick infrastructure to satisfy BRIEF §13.3 verbatim would require:
- Tick publisher in market-data-service (~150 LOC) — Binance WebSocket has aggTrade endpoint but v2 never wired it.
- Tick handler in PaperExchange (~80 LOC) — separate from existing `_on_candle` path.
- Tick consumer in shadow_worker FSM — orthogonal to live `lifecycle._step` candle-cadence.

Cross-cutting reach: T-511b1 + T-511b2 + T-512 (OHLC replay restart-recovery — name is OHLC-explicit) + T-513 (rejected-signal observation; mirrors persistence). All four would inherit the data-stream choice.

## Consequences

### Accepted

- **Live-shadow parity at candle cadence**: shadow worker FSM uses identical input granularity as live `lifecycle._step` → variant outcomes directly comparable to live trade outcomes. BE-trigger / trail-SL math identical (same Decimal inputs, same formulas — verified by T-511b2 BRIEF §13.7 parity test).
- **No new infrastructure**: zero new market-data-service code; zero new PE handlers. Reuses existing OHLC subscription path that T-511a already wires (`market.ohlc.1m.>` subscribe at PE `start_consuming`).
- **T-512 OHLC replay name self-consistent**: BRIEF §13.4 calls T-512 the "OHLC replay restart-recovery" task. Tick-based variants would have made T-512's name a misnomer.

### Trade-offs

- **No sub-minute granularity**: BE-trigger fires on candle close, not on first intra-candle tick crossing the threshold. Worst-case latency = 60 seconds vs tick-equivalent. For 4-hour `max_duration_hours` shadow lifetimes, this is <0.5% of variant duration.
- **Wick-sensitivity**: SL/TP cross detection uses candle high/low extremes (PE `_compute_intra_candle_path`); a wick that briefly crosses BE threshold and reverses within the same candle WILL fire BE-trigger. Live `lifecycle._step` has identical behaviour (same candle-grain detection) — parity holds.
- **H-008 signal TTL ambiguity**: signal TTL freshness check (signal received at t0, candle close at t0 + Δ) inherits candle-cadence freshness model, NOT tick-cadence. Existing T-209 signal-acceptance gating (BRIEF §15.2) uses candle freshness — this ADR aligns shadow with that decision.

### Rejected alternatives

- **Implement tick stream + tick-based shadow** (BRIEF §13.3 verbatim compliance): rejected. ~230 LOC new infrastructure for sub-minute granularity that adds <0.5% precision over candle-cadence on 4h lifetimes. Out-of-scope for F5 phase (would push shadow runtime cluster from 5 tasks → 7+ tasks).
- **Hybrid: live uses candle, shadow uses tick** (asymmetric): rejected. Live-shadow parity is the load-bearing F5 §13.7 invariant — variant outcomes must be directly comparable. Different cadences breaks the comparison guarantee.
- **File-local "spec-clarification" note in plan-doc only** (no ADR): rejected. Cross-cutting reach (T-511b1 + T-511b2 + T-512 + T-513 all inherit) makes this an architectural decision per §0.6 ADR discipline, NOT a single-task clarification. Plan-reviewer T-511b pass 1 flagged this as ADR-required (CONCERN 6).

## Implementation references

- `packages/exchange/paper/adapter.py:265-273` — T-511a `start_consuming` subscribes to `market.ohlc.1m.>`; ShadowWorker per-variant PE inherits this.
- `services/execution/app/lifecycle.py:_step` — live FSM operates on 1m candle cadence; T-511b1 shadow_worker.py mirrors candle-cadence FSM tick.
- BRIEF §13.4 — T-512 OHLC replay restart-recovery is by design the OHLC consumer; this ADR formalizes T-511b1/b2 + T-513 alignment.
- BRIEF §13.7 — parity test "variant `_step` transitions match live lifecycle FSM" requires identical input cadence; ADR locks this in.

## Supersedes / superseded by

None. Future work: if intra-candle precision becomes load-bearing (e.g., HFT-grade variants in F6+), revisit via new ADR + tick-stream infrastructure ticket.
