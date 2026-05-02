# F2 E1 — Bybit Testnet Smoke Runbook

**Purpose**: Manually verify the F2 execution flow against Bybit testnet end-to-end. Required by BRIEF §19 Phase F2 line 2527 ("a test webhook against Bybit testnet opens a position, places SL and TP, manages BE move and trail, closes correctly"). Operator-runnable; not CI per §11.6:1923.

**When to run**: Once before declaring F2 phase closed. Re-run after any execution-service change that touches placement, lifecycle, or close flow.

**Run cadence**: One full pass takes ~10–15 minutes including BE/trail wait windows. The runbook records run output in a follow-up `chore(F2-close)` commit.

---

## Prerequisites

- [ ] `.env` populated with `BOT_ALPHA_BYBIT_API_KEY` + `BOT_ALPHA_BYBIT_API_SECRET` for a Bybit testnet account.
- [ ] `BOT_ALPHA_BYBIT_SUB_ACCOUNT` matches the testnet sub-account used.
- [ ] `bots` table seeded with one row: `bot_id='alpha', exchange_mode='testnet', status='active'`.
- [ ] `docker compose up -d` shows `signal-gateway`, `execution-service`, `feature-engine`, `market-data-svc`, `nats`, `postgres` all healthy.
- [ ] `curl http://localhost:8000/health` (execution-service) returns `200`.
- [ ] `curl http://localhost:8000/ready` returns `200` and `body.adapter_pool == "ready"`.
- [ ] Tail prepared: `docker compose logs -f execution-service signal-gateway | tee /tmp/F2_E1_run.log`.

If any prerequisite fails, abort and resolve before proceeding.

---

## Step 1 — Webhook arrival

- [ ] POST a synthetic TradingView signal to `signal-gateway`:
      ```
      curl -X POST http://localhost:8001/webhook \
           -H "Authorization: Bearer $WEBHOOK_HMAC_SECRET" \
           -d '{"bot_id":"alpha","symbol":"BTCUSDT","side":"buy","qty":"0.001"}'
      ```
- [ ] Observe `signal_gateway.signal_accepted` log line.
- [ ] Verify `signals` table has a new row with `bot_id='alpha'`, `status='accepted'`.

_Exercises: §15.1 webhook HMAC auth (no §20 hazard pin)._

## Step 2 — Order placement

- [ ] Observe `placement.order_placed` log line; capture `correlation_id`.
- [ ] Observe `OrderPlaced` envelope on `orders.events.alpha` subject.
- [ ] Verify `orders` row with `status='filled'`, `exchange_order_id` populated.
- [ ] Verify `trades` row with `status='open'`, `entry_price` populated, `realized_pnl IS NULL`.
- [ ] Verify `position_state` row for `(alpha, BTCUSDT)` with `sl_type='protective'`.

_Exercises: H-003 (place_market_order non-idempotent, no retry), H-004 (SL-set failure demands emergency close)._

## Step 3 — Stop-loss set

- [ ] Observe `placement.set_trading_stop_sl` log line.
- [ ] Observe `SLMoved(sl_type='protective')` envelope on `orders.events.alpha`.
- [ ] Verify `position_state.sl_price` matches the configured SL distance from entry.

_Exercises: H-004 (SL retry on transient failure; if 3× fails, emergency close path triggers — observe `placement.emergency_close` if so)._

## Step 4 — Take-profit set

- [ ] Observe `placement.set_trading_stop_tp` log line.
- [ ] Verify Bybit position has TP registered (`tpsl_mode='Partial'` per H-013).
- [ ] Verify `position_state.tp_price` matches the configured TP distance from entry.

_Exercises: H-013 (partial TP mode explicit, never default)._

## Step 5 — BE trigger

- [ ] Wait for the testnet price to move favorable by ≥ `be_trigger` (per bot YAML; default ~0.3% from entry).
- [ ] Observe `lifecycle.be_triggered` log line within `position_poll_interval_s` (default 1s) of the trigger price.
- [ ] Observe `SLMoved(sl_type='be')` envelope on `orders.events.alpha`.
- [ ] Verify `position_state.sl_type == 'be'` and `sl_price` updated to `entry ± be_sl_level`.

_Exercises: H-018 (close/update by PK only — `position_state` composite-PK update)._

If price does not move ≥ `be_trigger` within ~5 minutes, abort and re-run when testnet liquidity allows.

## Step 6 — Trail update

- [ ] Continue waiting for further favorable move (after BE).
- [ ] Observe `lifecycle.trail_sl_adjusted` log line (one per trail tick after `partial_tp` or BE-and-trail-active).
- [ ] Observe `SLMoved(sl_type='trail')` envelope.
- [ ] Verify `position_state.sl_type == 'trail'` and `best_price` advancing in DB.

_Exercises: H-018 (PK-only updates on position_state); L-001 (POSITION_POLL_INTERVAL configurable)._

## Step 7 — Close

- [ ] Trigger close via either (a) waiting for SL hit, (b) waiting for full TP fill, or (c) manual close via Bybit testnet web UI (`reduce_only=True` market order).
- [ ] Observe `dispatcher.close_event_received` log line on size=0 position event.
- [ ] Observe `reconcile_close.closed_pnl_snapshot_pair` log lines (BEFORE + AFTER).
- [ ] Observe `OrderClosed` envelope with `realized_pnl` populated.
- [ ] Verify `trades` row updated: `status='closed'`, `realized_pnl` non-null, `closed_at` populated, `close_reason` set (`sl` / `manual` / `trail`).
- [ ] Verify `position_state` row deleted.

_Exercises: H-001 (cumulative-delta P&L source-of-truth), H-011 (post-close 2s sleep before AFTER snapshot), H-012 (per-trade attribution), H-018 (PK-only updates)._

## Step 8 — Audit-loop verification

- [ ] Wait `execution_audit_tick_interval_seconds` (default 300s = 5 min) after close.
- [ ] Verify `audit.no_divergence` log line for `sub_account=<testnet-sub>` — cumulative-delta and DB sums agree within `Decimal("0.50")` threshold.
- [ ] Verify NO `audit.pnl_divergence` ERROR log appeared.
- [ ] Verify NO row in `trade_pnl_deltas` table for this run window (no audit correction needed).

_Exercises: H-017 (audit attributes only via `trade_pnl_deltas`, never back-corrects `trades.realized_pnl`)._

## Step 9 — Sign-off

- [ ] Record run timestamp (UTC), `correlation_id` (from Step 2), final `realized_pnl` (from Step 7) in the follow-up `chore(F2-close)` commit body.
- [ ] Confirm `/tmp/F2_E1_run.log` archived alongside the commit (paste relevant log lines).
- [ ] Manually flat the testnet sub-account if any residual position remains (`Bybit testnet UI → Close All`).

_Exercises: §15.5 audit-grade run record._

---

## Failure modes to flag

If any of these occur, re-run is required before F2 close:

- `placement.emergency_close` triggered (H-004 SL exhaustion path) — investigate retry policy or testnet API health.
- `dispatcher.dedup_drop` count > 0 for non-duplicate `exec_id` — H-009 ring-buffer regression.
- `audit.pnl_divergence` non-zero — H-017 cumulative-delta mismatch; investigate before declaring F2 closed.
- `reconcile.orphan_db_closed` or `reconcile.orphan_exchange_market_closed` on next service restart — H-020 reconciliation reports orphan from this run; cleanup and re-run.

## Hazards explicitly NOT covered by this runbook

This single-bot manual smoke does NOT exercise:

- H-006 (webhook rate-limit threshold — single POST does not approach 20 req/60s).
- H-019 (score fail-open under indicator outage).
- H-021 (scheduled-job UTC hygiene — covered by ADR-0007 + AST regression test in T-220b).
- H-023 (shadow-variant restart via OHLC replay — F3 territory).
- H-025 (cross-bot IP rate-limit coordination — requires 2+ bots; covered by E3 unit test).
- H-026 (qty-step rounding edge — requires specific symbol/qty selection; covered by T-221 unit test).

These are covered by the corresponding F2 unit/integration tests, not by this manual smoke.
