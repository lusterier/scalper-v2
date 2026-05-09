# fix(T-217c-position-state-trade-id-guard) — composite-PK position_state UPDATE must include trade_id in WHERE clause (H-033)

**Type**: surgical bug fix (mirror T-218b/T-218c/T-216c pattern; not a new F5 numbered task; F5 phase counter UNCHANGED).
**Phase**: F5 (unlocked).
**Origin**: operator-discovered shipped-code bug 2026-05-08; Item 3 of 7-bug audit (per `docs/status.md` late-night XII 7-bug audit progress tracker).
**Date**: 2026-05-09.

## Bug

`packages/db/queries/execution.py:636-680` `update_position_state_after_fill` modifies `position_state` rows via composite PK `(bot_id, symbol)` only — `trade_id` is NOT in the WHERE clause. The dispatcher (`services/execution/app/dispatcher.py:225-232`) calls this helper after `_derive_exec_type` returns a `trade_id` derived from one of two sources:

- **Path A** (`order_id_match is not None`, `_derive_exec_type:344-358`): trade_id = `select_trade_by_open_order_id(order_id_match).id` OR `select_trade_by_close_order_id(order_id_match).id` — sourced from the `trades` table via the WS event's `exchange_order_id`.
- **Path B** (`order_id_match is None`, `_derive_exec_type:360-403`): trade_id = `select_position_state(bot_id, symbol).trade_id` — sourced from the position_state row itself (Path B by construction never mismatches).

Path A's trade_id and the position_state row's trade_id can DIVERGE under a benign close→open race:

1. Trade T1 (bot_id=alpha, symbol=BTCUSDT, trades.id=10) closes at time t. Close-flow runs `delete_position_state(bot_id, symbol)` to flatten.
2. New Trade T2 (bot_id=alpha, symbol=BTCUSDT, trades.id=11) opens at time t+1. `placement_persist` runs `insert_position_state(bot_id, symbol, trade_id=11)`.
3. WS event for T1's close fill arrives LATE (after T2's position_state row has been written) → `_derive_exec_type` Path A returns `trade_id=10` (via close_order_id lookup on T1).
4. `update_position_state_after_fill(bot_id="alpha", symbol="BTCUSDT", qty_delta=...)` modifies T2's row (composite PK match) using T1's qty_delta. **Wrong target row mutated.**
5. `update_trade_fees_incremental(trade_id=10)` writes fees to T1 correctly. But T2's `remaining_qty` was decremented incorrectly.
6. T2's `remaining_qty` may zero → close-trigger fires → T2 marked closed in DB while T2's position is still open on exchange → **phantom close cascade** (cumulative-delta P&L drift; reconcile_orphan flow; emergency_close on real position).

The mismatch is only possible in Path A because Path B sources `trade_id` directly from the position_state row.

### Real-world impact

- **Live**: Real money committed on T2 open; phantom-closed in DB → T-221 reconciliation eventually catches Bybit-OPEN/DB-CLOSED divergence → reconcile_orphan flow → emergency_close real position closure.
- **Paper**: No exchange-side equivalent; corruption stays in `position_state` / `trades`; could mask paper P&L drift.
- **Either mode**: Trade attribution for the late event becomes incorrect; subsequent fills on T2 with the corrupted `remaining_qty` produce inconsistent state.

The race window depends on:
- WS event delivery latency (Bybit typical ~50-200ms after fill confirmation; pathological cases under network load can be seconds).
- `placement_persist` → `insert_position_state` commit timing for the new T2 trade.
- Operator strategy is short-cycle scalping (1m–5m horizons per `extra/`); rapid close→reopen is the norm, not the exception.

## Why not surfaced earlier

- T-218b shipped 2026-05-08 fixed `exec_type="open"` not decrementing `remaining_qty` (H-030); same composite-PK assumption preserved.
- T-218a/T-218b/T-218c reviewer focus was the `exec_type` derivation branch table, the dispatch transaction shape, and the LIVE/paper dispatcher kill-paths — the WHERE-clause omission for `update_position_state_after_fill` was not surfaced because all existing test cases used a SINGLE trade per (bot_id, symbol) lifetime fixture.
- Operator's primary mode is paper per memory `deployment.md`; v2 multi-service NIE JE deployed; sibling v1 testnet stack disabled 2026-05-02; T-222 testnet smoke (F2 close-out) was never executed end-to-end. Race-window observation requires sustained live operation across multiple close→reopen cycles.
- L-020 active control (this fix) generalizes: composite-PK update helpers under concurrent INSERT/DELETE need authoritative-id verification at dispatch site when row identity can change between read and write.

## Operator decisions (2026-05-09 OQ session)

- **OQ-1 = B** (NOT default A): SQL WHERE trade_id extension. Update SQL helper signature to add `trade_id: int` parameter and include it in WHERE clause. 0-rows-updated → mismatch → halt. DB-enforced robustness; ripple effects propagate through helper signature, dispatcher caller, direct SQL helper tests.
- **OQ-2 = A**: Halt + raise on mismatch. `RuntimeError` with ERROR log key `execution.dispatcher_position_state_trade_id_mismatch`. Mirror existing `dispatcher_overfill_halt` / `dispatcher_orphan_synthetic_fill` / `dispatcher_unattributable_fill` patterns. T-221 reconciliation owns recovery.
- **OQ-3 = A**: NEW H-033 hazard entry in BRIEF §20 paired with H-030/H-031/H-032 cluster.
- **OQ-4 = A**: NEW L-020 review-lesson (composite-PK update authoritative-id verification pattern).

## Fix shape

### `packages/db/queries/execution.py` — SQL helper signature extension

`update_position_state_after_fill` signature gains required `trade_id: int` parameter:

```python
async def update_position_state_after_fill(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    trade_id: int,                           # NEW required kwarg
    qty_delta: Decimal,
    new_sl_type: Literal["protective", "be", "trail"] | None,
    updated_at: datetime,
) -> int:                                    # NEW return type: rows updated count
    """... extended docstring noting trade_id WHERE-clause guard per T-217c / H-033 ..."""
    if new_sl_type is None:
        result = await conn.execute(
            """
            UPDATE position_state
            SET remaining_qty = remaining_qty - $1, updated_at = $2
            WHERE bot_id = $3 AND symbol = $4 AND trade_id = $5
            """,
            qty_delta,
            updated_at,
            bot_id,
            symbol,
            trade_id,
        )
    else:
        result = await conn.execute(
            """
            UPDATE position_state
            SET remaining_qty = remaining_qty - $1, sl_type = $2, updated_at = $3
            WHERE bot_id = $4 AND symbol = $5 AND trade_id = $6
            """,
            qty_delta,
            new_sl_type,
            updated_at,
            bot_id,
            symbol,
            trade_id,
        )
    # asyncpg conn.execute returns "UPDATE <n>" status string for UPDATE statements.
    # Parse trailing int per asyncpg contract; tag-format is documented + stable.
    return int(result.split()[-1])
```

`asyncpg.connection.Connection.execute` returns the PostgreSQL command tag (e.g. `"UPDATE 1"` or `"UPDATE 0"`); parsing the trailing int is the standard idiom (used elsewhere in asyncpg-backed code; documented in asyncpg API reference).

### `services/execution/app/dispatcher.py` — caller threads trade_id + halts on 0 rows

The existing `if exec_type != "open":` block (lines 221-232) becomes:

```python
if exec_type != "open":
    new_sl_type: Literal["protective", "be", "trail"] | None = (
        "trail" if exec_type == "partial_tp" else None
    )
    # T-217c / H-033: composite-PK UPDATE must verify trade_id alignment.
    # _derive_exec_type Path A sources trade_id from `trades` table; if late
    # WS event arrives after close→reopen race, position_state row identity
    # may have changed (composite PK (bot_id, symbol) reused for new trade).
    # SQL helper now includes trade_id in WHERE; 0 rows updated → halt.
    assert trade_id is not None  # exec_type != "open" implies trade_id set per Path A/B
    rows_updated = await update_position_state_after_fill(
        conn,
        bot_id=self._bot_id,
        symbol=message.symbol,
        trade_id=trade_id,
        qty_delta=message.qty,
        new_sl_type=new_sl_type,
        updated_at=self._now_fn(),
    )
    if rows_updated == 0:
        self._bound_logger.error(
            "execution.dispatcher_position_state_trade_id_mismatch",
            bot_id=self._bot_id,
            symbol=message.symbol,
            event_trade_id=trade_id,
            exchange_exec_id=message.exchange_exec_id,
            exchange_order_id=message.exchange_order_id,
        )
        msg = (
            f"position_state.trade_id mismatch with derived trade_id={trade_id}: "
            "0 rows updated (composite PK row identity changed under concurrent "
            "close→reopen race)"
        )
        raise RuntimeError(msg)
```

The `assert trade_id is not None` is mypy-narrowing for the typed kwarg; runtime invariant is that exec_type != "open" + Path A/B both derive `trade_id` to a non-None value (Path A from trades table; Path B from position_state.trade_id). The defensive assert documents the invariant + crashes loudly if it ever drifts.

### Why this halts safely

- The dispatcher transaction is wrapped in `async with self._pool.acquire() as conn, conn.transaction()`. RuntimeError propagation rolls back the entire transaction (including the upstream `insert_execution` audit row). This is correct: the audit row + position_state mutation are EITHER both committed or both rolled back.
- NATS JetStream redelivery semantics: the dispatcher consumer is configured with explicit ack on success; raised exception means no ack → message re-delivered after server-side timeout. If the race condition resolves (T2 also closes, position_state deleted, T1's position_state never reappears), redelivery will produce a different terminal state (likely `unattributable_fill` halt — caller already documented). T-221 reconciliation owns recovery for both.

## Out of scope

- `update_position_state_monitor_tick` and `update_position_state_sl` — separate helpers with different call patterns; their callers (T-217 monitor, T-216b SL set) thread trade_id differently. Defer to follow-up if surfaced in operator audit.
- `delete_position_state` — already PK-bound and idempotent; no trade_id verification needed at delete time (delete is end-of-life for the row).
- `insert_position_state` — `insert` op; no race on identity (composite PK INSERT would conflict if duplicate, which is the desired safety property).
- Path B trade_id derivation pathway (synthetic fills without order_id_match): trade_id sourced from position_state.trade_id by construction → WHERE always matches → no mismatch detection needed for that branch (but the SQL guard does no harm there either).

## Files touched

### Source (2 files)

1. `packages/db/queries/execution.py` — `update_position_state_after_fill` signature + SQL extension. Add `trade_id: int` kwarg; include in WHERE clause; return `int` (rows updated count). Update docstring with H-033 + T-217c reference.
2. `services/execution/app/dispatcher.py` — caller threads `trade_id` to helper; checks return value `rows_updated`; on 0-rows-updated → ERROR log key `execution.dispatcher_position_state_trade_id_mismatch` + raise RuntimeError. Add `assert trade_id is not None` for mypy invariant pinning.

### Tests (3 files)

3. `packages/db/tests/test_queries_execution.py` — update existing 2 tests with new `trade_id` kwarg + `conn.execute` return value mocked as `"UPDATE 1"` so command-tag parser yields 1; add 1 NEW unit test `test_update_position_state_after_fill_returns_zero_when_command_tag_indicates_zero_rows` mocking `conn.execute = AsyncMock(return_value="UPDATE 0")` + asserting helper returns 0 + SQL string contains `trade_id = $N` in WHERE clause.
4. `tests/integration/queries/test_execution.py` — NEW testcontainer-gated integration test `test_update_position_state_after_fill_returns_zero_when_trade_id_mismatches` (real Postgres path per WG#7 + L-008 active control). Insert position_state row with trade_id=10, call helper with trade_id=11, assert return value == 0 + verify row UNCHANGED via `select_position_state` (remaining_qty intact, sl_type intact). Skip-gated on POSTGRES_TEST_DSN env var per existing F1 integration pattern.
5. `services/execution/tests/test_dispatcher.py` — update existing test expectations for `update_position_state_after_fill.kwargs` to include `trade_id` matching the test's fixture-derived value; add 1 NEW regression test `test_dispatcher_halts_on_position_state_trade_id_mismatch_during_fill_update` mocking `update_position_state_after_fill` mock `return_value=0` + asserting (a) ERROR log key `execution.dispatcher_position_state_trade_id_mismatch` with exact kwargs; (b) `RuntimeError` raised; (c) no downstream `update_trade_fees_incremental` invocation (transaction-rollback semantics).

### Documentation (4 files; chore commit)

5. `docs/CLAUDE_CODE_BRIEF.md` — NEW H-033 hazard entry between H-032 and §21 Glossary.
6. `docs/review-lessons.md` — NEW L-020 lesson appended after L-019.
7. `TASKS.md` — fix(T-217c) DONE entry at top of Done section; F5 phase counter UNCHANGED at 26/47.
8. `docs/status.md` — late-night XIII section prepended above late-night XII; updates 7-bug audit progress tracker (Item 3 → DONE).
9. `docs/plans/T-217c-fix-position-state-trade-id-guard.md` — this plan doc; staged in chore commit per CLAUDE.md gate-1 requirement.

## NEW H-033 hazard text (for BRIEF §20)

```
### H-033 — Composite-PK position_state UPDATE must include trade_id in WHERE clause

**Context.** `position_state` table uses composite PK `(bot_id, symbol)` per migration 0004. Under operator's short-cycle scalping (1m-5m horizons; rapid close→reopen pattern), the same `(bot_id, symbol)` row identity can host multiple trades sequentially: T1 opens → T1 closes → row deleted → T2 opens → T2's row written. WS execution events for the closing fill of T1 may arrive LATE after T2's `position_state` row exists. ExecutionDispatcher's `_derive_exec_type` Path A (`order_id_match is not None`) sources `trade_id` from the `trades` table via `select_trade_by_open_order_id` / `select_trade_by_close_order_id` — this trade_id is T1's. The subsequent `update_position_state_after_fill(bot_id, symbol)` (composite PK only) modifies T2's row using T1's qty_delta. Wrong target row mutation → `remaining_qty` corruption on T2 → potential phantom close cascade. Operator-discovered shipped-code bug 2026-05-08; fix shipped via `fix(T-217c-position-state-trade-id-guard)` precedent 2026-05-09.

**Policy.** `update_position_state_after_fill` SQL helper MUST include `trade_id` in the WHERE clause: `WHERE bot_id = $X AND symbol = $Y AND trade_id = $Z`. The helper returns `rows_updated: int` (parsed from asyncpg command tag `"UPDATE <n>"`). ExecutionDispatcher caller threads the derived `trade_id` (from `_derive_exec_type` Path A trades-table lookup or Path B position_state.trade_id) and halts on `rows_updated == 0`: ERROR log key `execution.dispatcher_position_state_trade_id_mismatch` + raise `RuntimeError("position_state.trade_id mismatch with derived trade_id=...")`. Transaction rolls back; NATS redelivery + T-221 reconciliation own recovery.

**Test.** `test_update_position_state_after_fill_returns_zero_when_trade_id_mismatches` (SQL helper); `test_dispatcher_halts_on_position_state_trade_id_mismatch_during_fill_update` (dispatcher integration via mocks).

H-033 numbering note: companion to H-030 (open-fill remaining_qty contract) + H-031 (paper adapter must not feed live ExecutionDispatcher) + H-032 (retry loop over external adapter call must catch transient exceptions). Together H-030/H-031/H-032/H-033 form the execution-service operational hardening cluster surfaced via operator audit 2026-05-08/05-09.
```

## NEW L-020 lesson text (for review-lessons.md)

```
## L-020 (fix(T-217c-position-state-trade-id-guard), operator-discovered shipped-code bug, 2026-05-08/05-09)
Pattern: Composite-PK SQL update helpers (e.g. `(bot_id, symbol)` for `position_state`) under concurrent INSERT/DELETE-then-INSERT need authoritative-id verification at dispatch site when row identity can change between the read that derives the id and the write that mutates the row. `update_position_state_after_fill(bot_id, symbol, ...)` was PK-bound on `(bot_id, symbol)` only; ExecutionDispatcher's `_derive_exec_type` Path A sourced `trade_id` from the `trades` table (via `select_trade_by_open_order_id` / `select_trade_by_close_order_id`); under a benign close→reopen race, the position_state row identity changed between derivation and write — the UPDATE silently mutated the wrong trade's row. Existing tests used SINGLE trade per (bot_id, symbol) lifetime fixtures, so the mismatch path was untestable. T-218b reviewer focus was the `exec_type` derivation branch table; the WHERE-clause omission for `update_position_state_after_fill` slipped through.
Active control: For any SQL UPDATE helper bound to a composite PK that can be reused across distinct logical entities (e.g. `(bot_id, symbol)` reused across sequential trades), plan-reviewer Gate 1 + brief-reviewer Gate 3 MUST grep callers and verify they thread an authoritative identity (e.g. `trade_id` from the entity's source table) AND the helper includes that identity in the WHERE clause. Helpers that return `int` (rows updated count) MUST have callers check the return for 0 and halt-on-mismatch (mirroring T-217c/H-033 dispatcher pattern: ERROR log + raise RuntimeError; T-221 reconciliation owns recovery). The pattern generalizes: any composite-PK UPDATE under concurrent identity churn is a hidden silent-corruption path unless the WHERE clause anchors to an immutable identity column.
```

## Write-time guidance

(Plan-reviewer APPROVE 2026-05-09 verbatim 10-item active control list; binding for drift-checker + brief-reviewer Gate 2/3.)

1. asyncpg command tag parser uses existing precedent `int(result.split()[-1])` from `packages/db/queries/analytics.py:2108` — match the idiom verbatim (no inline regex, no try/except — tag format is documented + stable per asyncpg API).
2. Helper docstring update must explicitly state return-value contract: "Returns rows updated count (parsed from asyncpg command tag). Caller MUST check `== 0` for trade_id mismatch detection — H-033 / T-217c."
3. Dispatcher kwarg threading: `assert trade_id is not None` MUST sit BEFORE the `update_position_state_after_fill` call, not after — runtime invariant per Path A/B both produce non-None when `exec_type != "open"`. Mypy narrows the typed kwarg via the assert; defensive crash documents the contract.
4. ERROR log key MUST be exactly `execution.dispatcher_position_state_trade_id_mismatch` (kwargs `bot_id`, `symbol`, `event_trade_id`, `exchange_exec_id`, `exchange_order_id`) per AC-6 mirror of `dispatcher_overfill_halt` / `dispatcher_orphan_synthetic_fill` / `dispatcher_unattributable_fill` log-key naming convention.
5. `test_dispatcher.py` existing kwargs assertions (~6-8 sites) MUST add explicit `kwargs["trade_id"]` verification matching the derived value from the test's fixture (Path A trade_id from `select_trade_by_*_order_id` mock OR Path B trade_id from `_ps_row(trade_id=N)` fixture); not just "kwarg present" but "kwarg value matches expected".
6. NEW dispatcher halt regression test `test_dispatcher_halts_on_position_state_trade_id_mismatch_during_fill_update` MUST set `update_position_state_after_fill` mock `return_value=0` (simulating mismatch) AND assert both: (a) ERROR log emitted with exact key + kwargs; (b) `RuntimeError` raised with message containing `"position_state.trade_id mismatch"`; (c) transaction state — verify no downstream `update_trade_fees_incremental` call (raises propagate before fees update — matches existing transaction-rollback semantics).
7. NEW direct SQL helper test `test_update_position_state_after_fill_returns_zero_when_trade_id_mismatches` MUST exercise REAL postgres path via testcontainer fixture (per L-008 active control: mock-only tests don't catch SQL parsing/binding issues). Insert position_state row with trade_id=10, call helper with trade_id=11, assert return value == 0 + verify row UNCHANGED (remaining_qty intact). Test placement: `tests/integration/queries/test_execution.py` (existing testcontainer-gated file per `test_queries_signal_gateway.py:5` cross-reference convention). Companion mock-only unit test in `packages/db/tests/test_queries_execution.py` covers the SQL-string + return-parser contract per file convention.
8. H-018 cross-reference in plan calls H-018 a "guideline"; H-018 brief text reads "All trade updates by `WHERE id = ?`" — H-018 is scoped to `trades` table (PK = `id`). For `position_state` (composite PK `(bot_id, symbol)`), H-018 doesn't strictly apply. The H-033 brief entry should clarify: "H-018 governs `trades` table single-PK updates; H-033 governs `position_state` composite-PK updates under identity-reuse — not a derogation of H-018."
9. H-033 brief entry MUST sit immediately after H-032 + before §21 Glossary marker (line `---` separator). Verify line ordering preserved at write time.
10. L-020 lesson MUST cite L-018 sibling reasoning ("plan claim 'dormant in mode' = unverified reasoning"): T-217c plan does NOT make a "dormant in mode" claim — explicitly states "Either mode" impact. L-018 active control compliance verified at plan time.

## Hand verification

N/A — no financial math. SQL extension + composition-only dispatcher caller change. Math-validator Gate 4 expected `VERIFIED — out of scope, math-validator skipped` (mirror T-218c/T-216c precedent: `services/execution/` + `packages/db/queries/` touched but no Decimal/float arithmetic, no indicator/seed convention, no P&L computation).

## LOC budget

- src delta:
  - `packages/db/queries/execution.py`: ~15 LOC (SQL extension + return type + docstring update).
  - `services/execution/app/dispatcher.py`: ~15 LOC (kwarg threading + 0-rows halt + log key + assert + comment block).
  - Total src: ~30 LOC.
- test delta:
  - `packages/db/tests/test_queries_execution.py`: ~15 LOC (2 existing test updates + 1 new mismatch test).
  - `services/execution/tests/test_dispatcher.py`: ~70 LOC (existing test kwarg updates across ~6-8 sites + 1 new dispatcher halt regression test).
  - Total tests: ~85 LOC.
- doc delta: BRIEF §20 H-033 (~30 LOC) + lessons L-020 (~12 LOC) + status.md late-night XIII (~50 LOC) + TASKS.md fix(T-217c) DONE entry (~50 LOC) + plan doc (this file).

Total feat commit: ~115 LOC; under §0.3 400 cap. Mirror surgical-fix cluster sibling sizes (T-218b ~98, T-218c ~151, T-216c ~84, T-217c ~115).

## Acceptance criteria (AC)

1. `packages/db/queries/execution.py` `update_position_state_after_fill` signature gains required kwarg `trade_id: int` and changes return type from `None` to `int` (rows updated parsed from asyncpg command tag).
2. SQL UPDATE statements (both branches: `new_sl_type is None` and `new_sl_type is not None`) include `AND trade_id = $N` in WHERE clause.
3. Helper docstring updated to cite T-217c / H-033 + return-value contract.
4. `services/execution/app/dispatcher.py` caller (existing `if exec_type != "open":` block at lines 221-232) threads derived `trade_id` to the helper kwarg.
5. `assert trade_id is not None` in the dispatcher caller (mypy-narrowing) BEFORE the helper call.
6. Dispatcher caller checks `rows_updated == 0` after the helper call; on mismatch → ERROR log key `execution.dispatcher_position_state_trade_id_mismatch` (kwargs `bot_id` + `symbol` + `event_trade_id` + `exchange_exec_id` + `exchange_order_id`) + raise `RuntimeError("position_state.trade_id mismatch with derived trade_id=...")`.
7. Existing `update_position_state_monitor_tick` + `update_position_state_sl` helpers UNCHANGED (out of scope per plan).
8. `packages/db/tests/test_queries_execution.py` updates 2 existing tests for the new kwarg + return-value contract; adds 1 NEW test `test_update_position_state_after_fill_returns_zero_when_trade_id_mismatches`.
9. `services/execution/tests/test_dispatcher.py` updates existing test sites that mock `update_position_state_after_fill` to assert `kwargs["trade_id"]` matches the expected derived value; adds 1 NEW test `test_dispatcher_halts_on_position_state_trade_id_mismatch_during_fill_update` proving the halt-on-mismatch path.
10. NEW H-033 entry in BRIEF §20 (after H-032, before §21 Glossary).
11. NEW L-020 lesson appended to docs/review-lessons.md (after L-019).
12. TASKS.md fix(T-217c) DONE entry at top of Done section; F5 phase counter UNCHANGED at 26/47.
13. docs/status.md late-night XIII section prepended above late-night XII; 7-bug audit progress tracker updates Item 3 → DONE.
14. Repo-wide pytest passes (baseline 2095 → 2097 expected; +2 net new tests; existing tests' kwarg updates stay net-zero count).
15. Commit shape per WG split: feat (src + tests) staged first; chore (TASKS + BRIEF + lessons + status + plan) staged second. **Branch**: `fix/T-217c-position-state-trade-id-guard` (RESTORING branch step after T-216c slip per CLAUDE.md branching policy + status.md late-night XII process slip note); FF-merge to master + push + branch delete.

## Test plan ordering (§N4 TDD)

1. Read existing test_queries_execution.py + test_dispatcher.py related sections.
2. Update SQL helper signature first (smallest blast radius) + run targeted helper tests — they will FAIL on missing `trade_id` kwarg (TypeError on call).
3. Update test_queries_execution.py 2 existing tests with kwarg + add 1 NEW mismatch test. Run: should PASS for the 2 updated + new mismatch test.
4. Update dispatcher.py caller to thread `trade_id` + check 0-rows + log + raise.
5. Update test_dispatcher.py existing tests with `kwargs["trade_id"]` assertions where they currently fixture-call `update_position_state_after_fill` mock + add 1 NEW dispatcher halt test. Run: should PASS.
6. Re-run targeted services/execution/tests/test_dispatcher.py + packages/db/tests/test_queries_execution.py.
7. Re-run repo-wide pytest — baseline 2095 + 2 new = 2097 expected; 0 regressions on the kwargs-updated existing tests.
8. Drift-checker Gate 2 → brief-reviewer Gate 3 → math-validator Gate 4 (out-of-scope).

Strict §N4 TDD ordering as in T-216c (write tests FIRST + verify FAIL pre-fix) is awkward here because the kwarg addition itself produces TypeError before the SQL guard's behavior can be observed. The TDD-spirit equivalent: implement SQL change + test changes in a single edit cycle, verify mismatch test FAILS without WHERE clause + PASSES with it (SQL-level TDD), then propagate caller wiring. This matches the §N4 intent (test-pinning of the new behavior) while accommodating the signature-change ripple.

## Open questions

None — all 4 OQs baked at plan time per operator session 2026-05-09:
- OQ-1 = B (NOT default A; SQL WHERE trade_id extension chosen over composition-only guard for DB-enforced robustness).
- OQ-2 = A (halt + raise on mismatch).
- OQ-3 = A (NEW H-033 hazard).
- OQ-4 = A (NEW L-020 lesson).

## Cross-references

- BRIEF §20 H-018 — UPDATE PK-only invariant. T-217c extends WHERE clause beyond PK with `trade_id` for race-detection — H-018 is a guideline (UPDATE WHERE id PK), H-033 is the documented exception when composite PK reuse + identity-change race exists.
- BRIEF §20 H-024 — `_derive_exec_type` branch table.
- BRIEF §20 H-030 — open-fill must not decrement remaining_qty (T-218b shipped 2026-05-08).
- BRIEF §20 H-031 — paper adapter must not feed live ExecutionDispatcher (T-218c shipped 2026-05-08).
- BRIEF §20 H-032 — retry loop over external adapter call must catch transient exceptions (T-216c shipped 2026-05-09).
- TASKS.md `## Done` T-218b / T-218c / T-216c — execution-service operational hardening cluster precedents.
- docs/status.md late-night XII — 7-bug operator audit progress tracker (Item 3 = T-217c subject).
- L-018 (T-218c lesson) — "dormant in mode" plan claims need code-citation evidence (none claimed here; both modes affected per Bug section).
- L-019 (T-216c lesson) — retry loops over external calls need exception handling at the await site.
- L-020 (this fix) — composite-PK UPDATE under concurrent identity churn needs WHERE-clause anchor to immutable identity column.

## Mirror precedents

- `fix(T-218b-open-fill-qty-bug)` 2026-05-08 — H-030; ~98 LOC; single-pass APPROVE; 4 gates clean.
- `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 — H-031; ~151 LOC; single-pass APPROVE; 4 gates clean.
- `fix(T-216c-fill-price-retry-exception)` 2026-05-09 — H-032; ~84 LOC; single-pass APPROVE; 4 gates clean.

T-217c expected to be mid-cluster size (~115 LOC); single-pass APPROVE expected; all 4 gates clean.

## Branch restore note

Per docs/status.md late-night XII process slip note: T-216c feat commit shipped directly on master (no branch). T-217c WILL use proper branch flow per CLAUDE.md branching policy:

1. `git checkout -b fix/T-217c-position-state-trade-id-guard` BEFORE staging any changes.
2. Feat commit on branch.
3. Chore commit on branch.
4. `git checkout master && git merge --ff-only fix/T-217c-position-state-trade-id-guard`.
5. `git push origin master`.
6. `git branch -d fix/T-217c-position-state-trade-id-guard`.
