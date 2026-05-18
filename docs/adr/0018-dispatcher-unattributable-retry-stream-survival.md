# ADR-0018 — Dispatcher unattributable-fill: bounded attribution-retry + per-bot stream-survival

- **Status:** Proposed (T-559). Promoted to Accepted after operator "proceed".
- **Date:** 2026-05-18
- **Amends:** T-216a WG#7 mid-flight per-bot-isolation stream-termination contract (`services/execution/app/dispatcher.py` `run_dispatcher_for_bot` :356-362) — for the unattributable-fill case only. Startup fail-fast and all other mid-flight integrity halts unchanged.
- **Related hazards:** NEW **H-039** (this ADR's enforcement site); **H-031** (paper-adapter-must-not-feed-live-dispatcher — orthogonal; paper-skip policy unaffected); **H-009** (dedup ring — not regressed); **H-030 / H-024** (not regressed).
- **Governance:** F6 post-MVP, ADR-0015 decision-C. Per-item ADR justified independently under §0.6/§6.3 because T-559 amends a documented cross-cutting multi-site contract (not mere phase admissibility).

## Context

`run_dispatcher_for_bot` consumes the exchange **WS** execution stream (`adapter.stream_executions()` — NOT a NATS pull consumer; no JetStream ack/nak/redelivery on this path) and feeds each event to `_process`. Any `Exception` in `_process` → `bound_logger.error("execution.dispatcher_stream_terminated")` + re-raise → that bot's dispatcher task dies (service stays up via lifespan `gather(return_exceptions=True)`; per-bot isolation by design — T-216a WG#7; recovery only on service restart → T-221 reconcile).

`_process` raises `RuntimeError("unattributable fill: no order match and no position_state")` (:192-200) when `select_order_id_by_exchange_id` returns None and `_derive_exec_type` returns `("unknown", None, None)`. This fires on the **WS-fill-before-persist race**: the open-fill WS event for a freshly REST-placed order arrives before the `placement_persist` transaction commits `orders` / `trades` / `position_state`. The race is transient — placement-persist commits sub-second. But terminating the bot's entire execution stream — which also carries that bot's SL/TP/close fills — for a self-resolving condition is a disproportionate, capital-unsafe failure: the just-opened exchange position is left untracked and SL-less until a service restart.

Real-world manifestation 2026-05-18 (demo bot, signal id=3 / Bybit order `79a8544f`): WS open-fill beat placement-persist → `dispatcher_exec_type_unknown → dispatcher_unattributable_fill → dispatcher_stream_terminated` → zero execution-side rows persisted → operator had to manually close an untracked SL-less demo position. Consolidated repro: `docs/status.md` 2026-05-18 "full stack redeploy" block.

## Decision

Introduce a bounded attribution-readiness retry **before** the `_process` write-transaction, yielding three distinct, separately-logged outcomes:

1. **Attributable within window** — proceed into the existing write-tx body unchanged (exec_type re-derived in-tx as today; full read+insert+update atomicity preserved).
2. **Window exhausted (genuine orphan / persistence never arrived)** — `bound_logger.error("execution.dispatcher_unattributable_fill_skipped")` + an `insert_trading_event(event_type="dispatcher_unattributable_fill_skipped")` audit row (§N2 audit-grade; emit-only, mirrors `trail_audit.py`; **NO** NATS event — §0.8, no consumer) + `return` (skip the event, **NO raise**) → the async-for continues → **the per-bot stream survives**.
3. **Gate passed but in-tx `_derive_exec_type` still `("unknown", None, None)`** — the existing `:192-200` raise is **kept as a defensive in-tx invariant** (re-keyed to a distinct `execution.dispatcher_unattributable_fill_post_gate` log) → `RuntimeError` → tx rollback → `dispatcher_stream_terminated`. This is correct fail-stop: the pre-tx gate probe and the in-tx re-derivation read different DB snapshots at different instants (non-atomic; a concurrent close→reopen / partial-visibility window can render a gate-passed event in-tx-unattributable) — a genuine integrity anomaly of the same class as the other in-tx integrity halts (over-fill / orphan_order / orphan_synthetic / ps-mismatch), which are explicitly out of scope and unchanged.

The retry runs in-process within a single `consume`/`_process` invocation; the H-009 dedup ring (keyed `exchange_exec_id`) therefore sees exactly one processing — no redelivery, no double-apply. The backoff sleep is conn-free (no pooled connection held, no open transaction across the sleep). Attempts and backoff are §N9 config (`execution_dispatcher_unattributable_retry_attempts`, `execution_dispatcher_unattributable_retry_backoff_s`), mirroring the T-556 `execution_fill_price_retry_*` precedent.

Genuine-orphan recovery remains T-221 reconcile's domain; the missing-SL backstop for a genuinely-orphan exchange position is the H-028 SL-watchdog.

## Consequences

- **(+)** The transient WS-fill-before-persist race — the common, real case — no longer loses tracking or strands an SL-less position.
- **(+)** The bot's execution stream is resilient to a single anomalous fill: extends the per-bot-isolation philosophy from "don't crash the service" to "don't crash the bot's stream for one non-fatal fill".
- **(−)** A genuine orphan fill is now skipped (audited + alertable via the `trading_events` row) rather than fail-stopping the stream. Accepted: genuine orphans are rare; T-221 + H-028 are the designed backstops; a skipped+audited orphan is strictly safer than a dead stream that also drops that bot's SL/TP/close fills.
- **(−)** A racing fill incurs bounded added latency (≤ the §N9 window, default ~5 s) — far below any trade horizon.
- Outcome-3 preserves the strong fail-stop for the rare genuine post-gate integrity anomaly, so the audit guarantee for true integrity violations is undiminished.

## Alternatives rejected

- **Keep fail-stop after retry** (retry, but still raise→stream_terminated if exhausted): leaves the exact untracked-SL hole for genuine orphans; weaker on the specific safety concern that motivated the task.
- **Skip-only, no retry** (never raise on unattributable; always skip+continue): even the transient race would briefly strand a position and drop the open-fill audit row until T-221 reconcile.
- **NATS redelivery / DLQ**: unavailable on this path — `run_dispatcher_for_bot` consumes an exchange WS async-iterator, not a JetStream pull consumer, so there is no ack/nak/redelivery primitive.
