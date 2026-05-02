# ADR-0006: Cumulative-delta P&L reconciliation strategy

Status: accepted
Date: 2026-05-02
Deciders: operator, Claude Code
Prerequisite for: T-219 (replaces `reconcile_close` NotImplementedError stub).

## Context

Brief §9.5:1592-1599 mandates cumulative-delta reconciliation as the close-flow attribution strategy in v2. v1 used naive `closed-pnl orderId → DB orderId` matching which corrupted $73 of P&L (H-001 catalog entry). Brief §20 hazards H-001 / H-002 / H-011 / H-012 each pin a specific behavior the close-flow must satisfy.

T-218b dispatcher already wires the call site: when an execution event drives `position_state.remaining_qty` to 0, the dispatcher invokes `reconcile_close(...)` inside the same `conn.transaction()` block. That call currently raises `NotImplementedError("T-219: cumulative-delta close flow + update_trade_close + delete_position_state")`. T-219 must replace this with the real flow.

The brief's high-level recipe is unambiguous (§9.5:1594-1599):

1. Snapshot `closed_pnl_total` BEFORE close.
2. Wait (H-011: configurable 2s sleep).
3. Snapshot AFTER.
4. Delta = attributable realized P&L. Apportion to trades in close order.

But several operational details are not pinned by the brief and need this ADR to lock them before T-219 plan-reviewer:

- **Snapshot timing under partial-TP.** A partial-TP fill produces a `closed_pnl` row but is NOT a full close. Should the reconciler snapshot only on the FINAL close (when remaining_qty hits 0), or on EVERY size-decreasing fill? The brief says "before any close flow begins", implying only at close-trigger.
- **2s sleep configurability and rationale.** H-011 says "2s configurable". Where does the sleep parameter live? What's the operationally tunable range? Is the sleep before the AFTER snapshot only, or also before the BEFORE snapshot (less likely)?
- **Attribution order under multiple `closed_pnl` rows.** A position closed via partial-TP-then-SL produces 2+ rows in Bybit's `/v5/position/closed-pnl` response between the BEFORE and AFTER snapshots. How to attribute the delta back to per-trade `realized_pnl` when the dispatcher only knows about the close that triggered the snapshot pair?
- **Concurrent closes across bots sharing a sub-account.** Two bots `alpha` + `beta` on the same Bybit sub-account, both close in overlapping windows → both invoke `get_closed_pnl_cumulative(sub_account)` and both see each other's deltas. Brief §9.5:1599 says "concurrent closes per sub-account isolation" — but how exactly?

This ADR locks the operational answers so T-219 plan-reviewer has a concrete contract to verify against.

## Decision

### D1 — Snapshot pair triggers ONLY on full-close (remaining_qty=0 post-fill)

`reconcile_close` is invoked exactly once per trade close — at the dispatcher's close-trigger branch (`ps_after.remaining_qty == Decimal("0")`). Partial-TP fills do NOT trigger snapshot pairs. Rationale:

- Partial-TP attribution would require a per-fill snapshot pair, doubling the API call rate (each pair = 2 × `get_closed_pnl_cumulative` = 2 × paginated REST scans).
- Cumulative-delta semantics already absorb partial-TP into the AFTER snapshot at the final close. The reconciler reads the cumulative delta across ALL `closed_pnl` rows produced between BEFORE and AFTER, which includes the partial-TP rows alongside the SL/trail closeout.
- Brief §9.5:1592 explicitly says "On size=0 event, invoke `_close_trade`". Partial-TP fills emit size>0 events (T-218b dispatcher path); only the final close (size=0) drives the reconciler.
- This matches T-218b dispatcher's existing close-trigger logic (`ps_after.remaining_qty == Decimal("0")` per WG#2 in T-218b plan).

### D2 — 2s sleep is BEFORE the AFTER snapshot ONLY; configurable via Settings

`Settings.execution_closed_pnl_post_close_sleep_s: float = 2.0` (mirror existing F2 Settings naming convention). Sleep happens once per `reconcile_close` call:

```
1. before_total = await adapter.get_closed_pnl_cumulative(sub_account)
2. ... (caller's tx commits any persistence updates, but the reconciler stays inside the tx) ...
3. await asyncio.sleep(settings.execution_closed_pnl_post_close_sleep_s)
4. after_total = await adapter.get_closed_pnl_cumulative(sub_account)
5. delta = after_total - before_total
```

No sleep before the BEFORE snapshot — the BEFORE is a baseline read, taken at the start of the reconciler call. Bybit's `closed-pnl` reflects past closes; no race on the BEFORE side.

The sleep is configurable per §N9. Range: 0.0 (disabled, for tests) to 30.0 (operationally bounded — longer would risk intervening closes from other bots polluting the delta). Settings field defaults to 2.0 per H-011.

### D3 — Attribution: full delta to the single trade triggering the snapshot pair

When `reconcile_close(trade_id=N, ...)` is invoked, the entire `delta = after_total - before_total` is attributed to trade `N` via `update_trade_close(trade_id=N, realized_pnl=delta, ...)`. **Per-trade attribution is single-trade, single-call.**

Rationale:

- The dispatcher's close-trigger fires exactly once per trade close (D1). One trade per snapshot pair.
- Partial-TP rows produced between BEFORE and AFTER are NOT separate trades — they're component fills of THIS trade. The cumulative delta correctly sums them.
- If two trades close back-to-back on the same sub-account (e.g., bot's bot_id=alpha closes BTCUSDT, then 100ms later same bot closes ETHUSDT), each invocation gets its own snapshot pair. Trade 1's snapshot pair captures only its own `closed_pnl` rows because trade 2 hasn't yet contributed to AFTER snapshot. Trade 2's snapshot pair starts from a BEFORE that already includes trade 1's delta — so trade 2's delta = trade2_only.

This works as long as `reconcile_close` calls **serialize** at the dispatcher level. Per T-218b's design, `_process` runs sequentially per dispatcher (single asyncio.Task per bot); two close-triggers in the same bot serialize naturally. Cross-bot serialization is handled by D4.

### D4 — Cross-bot closes on shared sub-account: serialize via per-sub-account asyncio.Lock

Multiple bots may share a Bybit sub-account (per ADR-0004 H-022 family — env-var indirection allows e.g. `BOT_ALPHA_BYBIT_SUB_ACCOUNT=demo-1` and `BOT_BETA_BYBIT_SUB_ACCOUNT=demo-1` to map both to the same account). If alpha and beta close in overlapping windows on the same sub-account, naive parallel snapshots would interleave:

```
T+0:    alpha calls get_closed_pnl_cumulative → BEFORE_A
T+0.1:  beta calls get_closed_pnl_cumulative → BEFORE_B == BEFORE_A
T+0.5:  alpha's actual close materialises in Bybit
T+1:    beta's actual close materialises in Bybit
T+2:    alpha calls get_closed_pnl_cumulative → AFTER_A (includes BOTH closes)
T+2.1:  beta calls get_closed_pnl_cumulative → AFTER_B (includes BOTH closes)
```

Both alpha and beta would attribute the SAME combined delta to themselves → double-counting.

**Solution**: a per-sub-account `asyncio.Lock` held across the BEFORE→sleep→AFTER triplet. Lock is keyed on `sub_account` string (not bot_id) because the resource being protected is the sub-account's `closed-pnl` ledger.

Implementation:

- Lifespan creates `closed_pnl_locks: dict[str, asyncio.Lock]` keyed on sub_account string. Each bot's adapter contributes its sub_account to the dict at lifespan setup; if a sub_account string is shared across bots, both bots reference the same Lock instance.
- `reconcile_close` acquires the Lock before BEFORE snapshot, releases after AFTER snapshot. Inside the Lock: snapshot + sleep + snapshot + delta computation + persistence.
- The Lock is single-process; if execution-service is later sharded across processes, a NATS KV-backed distributed lock would replace this (out-of-scope for F2 single-process service per §3.1).

Scope-aware: F2 single-process execution-service per §3.1 → asyncio.Lock is sufficient. F5+ sharding revisit if needed.

### D5 — `update_trade_close` writes realized_pnl + fees_paid + close_reason atomically

Inside the `reconcile_close` tx (caller's `conn.transaction()`):

1. `update_trade_close(trade_id, realized_pnl=delta, fees_paid=ps.fees_paid_at_close, close_reason=<derived>, exit_price=event.price, closed_at=event.executed_at, close_order_id=order_id_match)` — single PK-keyed UPDATE per H-018.
2. `delete_position_state(bot_id, symbol)` — composite PK DELETE.
3. Emit `OrderClosed` event to `orders.events.<bot_id>` post-commit (Q2 publish-after-persist; T-216b2 precedent).

`close_reason` derivation: T-218b dispatcher passes `exec_type` to `reconcile_close`; the reconciler maps it:
- `'close'` → `'manual'` (operator-initiated close order).
- `'sl'` / `'trail'` → `'sl'` / `'trail'` (the exec_type IS the close reason).
- `'unknown'` → `'unknown'` (operator-actionable; T-220 audit may correct).

`fees_paid` already accumulates incrementally per fill via T-218b `update_trade_fees_incremental`. The reconciler does NOT add to `fees_paid` — it just reads the current value from the trades row (or from `ps` if cached) and re-writes through `update_trade_close` for atomic finalization with the realized_pnl write.

**`close_order_id` synthetic-fill resolution**: `update_trade_close` requires `close_order_id: int` (NOT NULL). For synthetic close fills (trail/SL without an `executions.order_id` match), T-218b's helper `select_open_order_id_by_trade_id` resolves `close_order_id` to the OPEN order_id (anchor convention from T-218b plan §"MODIFIED packages/db/queries/execution.py" — every fill is anchored to a placed-order context, even if the fill itself wasn't issued against that order). T-219 reuses this resolution: when `order_id_match` (passed from dispatcher) is None on synthetic-close paths, the reconciler reads `select_open_order_id_by_trade_id(trade_id)` to satisfy the FK NOT NULL invariant.

### D6 — Hazard test pin mapping

The brief §20 lists 4 hazard-named tests T-219 must satisfy; this ADR maps each to its semantic anchor:

- **H-001** `test_cumulative_delta_ignores_order_ids`: fixture sets up `closed_pnl` rows with **mismatched orderIds** (Bybit's CLOSE orderId vs DB OPEN orderId per H-001 incident). Reconciler computes delta from cumulative totals only; orderIds are NEVER read from the response. Test asserts the delta computation works with deliberately-permuted orderIds. ADR pin: `_compute_delta` reads ONLY `total - total`, not per-row `orderId`.
- **H-002** `test_close_with_identical_prior_trade_same_symbol`: fixture has a recently-closed trade with identical `(symbol, qty, entry_price)` to the current close. v1 entry+qty matching would have collided. Reconciler's cumulative-delta approach has no matching at all, so this scenario produces the correct per-trade delta. ADR pin: D1+D3 (single-trade, single-snapshot-pair, full-delta attribution) makes the scenario non-pathological.
- **H-011** `test_closed_pnl_snapshot_waits_before_reading`: fixture verifies the 2s sleep is honored (or whatever Settings configures). Test uses `monkeypatch` on `asyncio.sleep` to verify call. ADR pin: D2 single-sleep-before-AFTER-only contract.
- **H-012** `test_close_uses_closed_pnl_delta_over_ws_accumulation`: fixture has a WS-accumulated `running_pnl` that DIFFERS from the `closed-pnl` cumulative delta (e.g., WS missed a fill due to reconnect mid-tick). Reconciler MUST persist the closed-pnl-delta value, NOT the WS-accumulated value. ADR pin: D5 `update_trade_close(realized_pnl=delta, ...)` where `delta` comes from snapshot pair, not from `position_state.running_pnl`.

## Rationale

- **D1 single-trigger semantics**: matches T-218b dispatcher's existing close-trigger boundary; avoids per-fill snapshot pair API cost; aligns with brief §9.5:1592 "size=0 event invokes _close_trade".
- **D2 single 2s sleep before AFTER**: brief §9.5:1596 "snapshot again" (after close) plus H-011 "2s sleep is kept before the 'after' snapshot for margin". Configurability per §N9 + L-001 active control.
- **D3 single-trade single-call attribution**: aligns with D1; works because dispatcher serializes per-bot via single asyncio.Task. Cross-bot serialization handled by D4.
- **D4 per-sub-account asyncio.Lock**: minimal-footprint protection against shared-sub-account interleaving; F2-scope-appropriate (single-process service); revisit at F5+ sharding.
- **D5 atomic close persistence**: extends T-216b1 `update_trade_close` PK-only contract; reuses T-218a `delete_position_state`; emit pattern mirrors T-216b2.
- **D6 explicit hazard-test mapping**: turns brief §20 test names into checkable contract pins for T-219 plan-reviewer.

## Consequences

Positive:
- T-219 plan-reviewer has a concrete contract: 4 hazard test pins map to 5 ADR decisions; deviations require ADR amendment.
- `reconcile_close` signature additions for T-219 are scoped: needs `adapter: ExchangeClient` (D2 snapshot calls) + `sub_account_lock: asyncio.Lock` (D4) + `settings: Settings` (D2 sleep param). Plan-reviewer can verify ctor changes against this contract.
- Cross-bot shared-sub-account scenarios are explicitly covered (D4); future operator can run alpha+beta on demo sub-account without P&L corruption.
- Per-fill snapshot avoidance (D1) bounds API cost: at most 2 × `get_closed_pnl_cumulative` calls per closed trade. With paginated responses (`_MAX_CLOSED_PNL_PAGES` per BybitV5Adapter), worst-case API cost is bounded.

Negative / trade-offs:
- D4's asyncio.Lock is single-process only. F5+ sharding requires distributed coordination (deferred per §0.8).
- D2's 2s sleep adds 2s latency per close — operationally acceptable for scalper close cadence (closes are seconds-to-minutes apart, not sub-second).
- D3 attributes full delta to the close-triggering trade. If a manual operator-initiated close order somehow interleaves with a bot's automatic close in a way D4 doesn't catch (e.g., operator closes via Bybit UI, not via execution-service), the bot's snapshot pair would absorb the operator's delta. Mitigation: T-220 P&L audit catches divergence > $0.50 (per §9.5:1601-1605); operator-initiated UI closes are out-of-scope for execution-service P&L.
- Locks are per-process; a Bybit-side rate-limit on `/v5/position/closed-pnl` (sub-account-scoped via shared-IP family per H-025) interacts with the lock: while one bot holds the Lock + waits 2s, the OTHER bot is queued behind the Lock + will then ALSO need the rate-limit token. SharedRateLimiter already handles the rate-limit token; the Lock just prevents snapshot interleaving. Composability verified.

## Alternatives considered

- **Per-fill snapshot pair attribution** (rejected): doubles API cost, complicates per-trade delta logic, no upside vs D1+D3 because cumulative semantics already absorb partial-TP fills into the final-close delta.
- **Sleep before BOTH snapshots** (rejected): wastes 2s of latency. BEFORE is a baseline read; Bybit `closed-pnl` is past-state; no race on the BEFORE side.
- **No cross-bot lock** (rejected): would corrupt P&L on shared-sub-account interleaving. ADR-0004 explicitly allows multiple bots → one sub-account; D4 is the cost of that flexibility.
- **DB-backed lock instead of asyncio.Lock** (rejected for F2): DB lock would handle multi-process sharding but is over-engineered for F2 single-process service. F5+ sharding revisits.
- **WS-accumulated running_pnl as source of truth** (rejected per H-012): WS gaps from reconnect mid-tick would silently corrupt P&L. Closed-pnl is authoritative per Bybit, so v2 uses it.

## Cross-references

- Brief §9.5:1592-1599 (cumulative-delta close-flow recipe).
- Brief §20 H-001 (P&L orderId matching unsafe), H-002 (entry+qty collision), H-011 (2s sleep), H-012 (closed-pnl source of truth).
- Brief §3.1 (single-process execution-service).
- ADR-0004 (sub_account env-var family — multiple bots may share sub_account).
- ADR-0005 (H-024 v2 trail semantic — orthogonal to this ADR but cited as concurrent close-flow context).
- T-218b plan `docs/plans/T-218b.md` (dispatcher close-trigger boundary that calls reconcile_close).
- T-208a `packages/exchange/bybit_v5/adapter.py:299-335` (`get_closed_pnl_cumulative` impl with `_MAX_CLOSED_PNL_PAGES` pagination cap).
- T-208b `packages/exchange/paper/persistence.py:471` (paper-mode closed_pnl simulation).
- T-216b1 `packages/db/queries/execution.py:287` (`update_trade_close` PK-only invariant).
- T-218a `packages/db/queries/execution.py:delete_position_state` (composite-PK DELETE).

## Follow-up

- **T-219** (this ADR's primary consumer): replaces `reconcile_close` `NotImplementedError` stub with body per D1-D5; ctor signature gains `adapter`, `sub_account_lock`, `settings`; tests pin H-001/H-002/H-011/H-012 per D6 mapping.
- **T-220** (P&L audit loop, blocked by T-208 + ADR-Q-C): cross-checks `trades.realized_pnl` against fresh `closed-pnl` reads every 5min; correction threshold $0.50; complements T-219's close-time delta.
- **F5+ revisit**: if execution-service is sharded across processes (e.g., per-bot worker pool), D4's asyncio.Lock must be replaced with a distributed lock (NATS KV-backed CAS or PG advisory lock). Operator-driven trigger.
