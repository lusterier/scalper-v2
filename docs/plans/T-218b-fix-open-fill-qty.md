# fix(T-218b-open-fill-qty-bug) — Open fill must not decrement remaining_qty (H-030)

**Phase:** F5 (urgent fix; does NOT count toward F5 numbered task counter — mirror fix(T-511b2a) urgent-fix precedent which was a ci-full follow-up).
**Type:** Critical bug fix; pre-live blocker.
**Hazards bound:** H-030 (NEW — open-fill must not decrement remaining_qty).

## Bug analysis (operator-confirmed 2026-05-08)

Verified via shipped-code review. Reproduction chain:

1. **Placement tx** (placement_persist.py:419-432) — `insert_position_state(remaining_qty=request.qty, ...)` writes full position size at trade-open commit.
2. **Execution event arrives via WS stream** — Bybit V5 publishes execution event for the open fill on `orders.events.<bot_id>`. ExecutionDispatcher consumes via `stream_executions`.
3. **`_derive_exec_type`** (dispatcher.py:328-331) — when `order_id_match` exists and trade has matched order as `open_order_id`, returns `("open", trade.id, None)`.
4. **`insert_execution(exec_type="open")`** (dispatcher.py:198-211) — writes audit row. Correct.
5. **`update_position_state_after_fill(qty_delta=message.qty, new_sl_type=None)`** (dispatcher.py:213-223) — UNCONDITIONALLY subtracts qty. SQL at execution.py:660 does `remaining_qty = remaining_qty - $1`. With qty_delta=full_qty entering full remaining_qty → **remaining_qty = 0**.
6. **Close-trigger fires** (dispatcher.py:237-258) — `if ps_after.remaining_qty == Decimal("0")` → `reconcile_close(...)` marks trade as closed + publishes OrderClosed event + `trade.closed.<bot_id>` event.

**Real-world impact** (live/testnet):
- Position opens on exchange (real money committed) but DB tracks it as closed immediately after open-fill WS event.
- Cumulative-delta P&L audit (T-220, ADR-0007) sees phantom close → trade_pnl_deltas drift.
- Shadow variants for trade → `trade.closed.>` event → `ShadowWorker._on_parent_close` cancel hook fires → variants spurious-cancelled.
- Reconcile-on-startup (T-221, H-020+H-026) on next service restart sees Bybit reports position OPEN, DB reports trade CLOSED → reconcile_orphan flow → may trigger emergency_close → real position closed on exchange.

**Why not surfaced earlier**:
- Operator's primary mode is paper. PaperExchange synthetic-fill flow (per `_drain_partial_tp` / `_drain_full_close` in persistence.py) emits ExecutionEvent for SL/TP fills only; for OPEN fills, PaperExchange writes paper_orders + paper_trades + paper_positions in single tx (no separate execution event published to `stream_executions` for the open). **Bug dormant in paper mode.** To verify at fix-time.
- T-222 testnet smoke (F2 close-out) was NEVER executed end-to-end; sibling v1 testnet stack disabled 2026-05-02; v2 multi-service NIE JE deployed (per memory `deployment.md`).
- Existing `test_process_open_fill_orders_lookup_to_open_branch` (test_dispatcher.py:366-386) uses **artificial mock** `select_position_state.return_value = _ps_row(remaining_qty=Decimal("5"))` to bypass close-trigger. Test does NOT pin pre-update→post-update equality for open fill; tests the wrong invariant.

## Operator decisions baked (session 2026-05-08)

- **OQ-1 [BAKED]**: Bug analysis approved. Fix approach approved.
- **OQ-2 [BAKED]**: Defensive close-trigger guard `if exec_type != "open" and ps_after is not None and ps_after.remaining_qty == Decimal("0"):` — guards against state-inconsistency edge case where some other path zeroes remaining_qty during open processing.
- **OQ-3 [BAKED]**: Patch as `fix(T-218b-open-fill-qty-bug)` urgent-fix on master mirror `fix(T-511b2a)` precedent — NOT new T-NNN task in F5 hardening cluster (operator's preference for urgency vs F5 task accounting).

## Scope

**In scope**:
- `services/execution/app/dispatcher.py` — wrap `update_position_state_after_fill` in `if exec_type != "open":` conditional + add defensive guard at close-trigger check.
- `services/execution/tests/test_dispatcher.py` — fix existing `test_process_open_fill_orders_lookup_to_open_branch` to assert realistic semantics (remaining_qty pre-fill = full qty; post-fill = full qty unchanged); add NEW regression test `test_process_open_fill_does_not_decrement_remaining_qty`.
- `docs/CLAUDE_CODE_BRIEF.md` §20 — NEW H-030 hazard entry (after H-026; H-027/H-028/H-029 reserved for ADR-0011 anticipated hazards per T-525/T-534/T-535).
- `docs/review-lessons.md` — NEW L-017 lesson (test fixtures using artificial post-update values can hide bugs; tests must use realistic placement-time entering values for state-mutation tests).
- TASKS.md — fix(T-218b-...) entry to Done section.
- docs/status.md — late-night X section.

**Out of scope**:
- PaperExchange open-fill stream verification — defer to T-516a2 plan stage or T-525 plan stage if relevant. To be confirmed at fix-time but not blocking the fix itself.
- Refactor `update_position_state_after_fill` to take exec_type parameter (would require call-site updates across multiple callers; operator-decided minimal-fix scope per OQ-3).

## Files touched

```
services/execution/app/dispatcher.py                   M  (~12 LOC delta; conditional + close-trigger guard)
services/execution/tests/test_dispatcher.py            M  (~80 LOC delta; fix existing + 1 new regression test)
docs/CLAUDE_CODE_BRIEF.md                              M  (~20 LOC; H-030 hazard entry)
docs/review-lessons.md                                 M  (~10 LOC; L-017 lesson)
docs/plans/T-218b-fix-open-fill-qty.md                 NEW (this plan; ~150 LOC)
TASKS.md                                               M  (chore commit; fix(T-218b) DONE entry)
docs/status.md                                         M  (chore commit; late-night X section)
```

## The fix

**dispatcher.py** (replace lines 213-223):

```python
# T-218b fix(open-fill-qty-bug): exec_type="open" must NOT decrement remaining_qty.
# Placement tx (placement_persist.py:419) sets remaining_qty=request.qty; the
# WS execution event for the open fill is audit-side mirror only (insert_execution
# above writes the audit row). Fees still incremented unconditionally below.
if exec_type != "open":
    new_sl_type: Literal["protective", "be", "trail"] | None = (
        "trail" if exec_type == "partial_tp" else None
    )
    await update_position_state_after_fill(
        conn,
        bot_id=self._bot_id,
        symbol=message.symbol,
        qty_delta=message.qty,
        new_sl_type=new_sl_type,
        updated_at=self._now_fn(),
    )
```

**dispatcher.py** (replace line 237 with defensive guard per operator OQ-2):

```python
ps_after = await select_position_state(
    conn,
    bot_id=self._bot_id,
    symbol=message.symbol,
)
if exec_type != "open" and ps_after is not None and ps_after.remaining_qty == Decimal("0"):
    # H-030 defensive guard: open-fill must NEVER trigger close-flow even
    # if some other path zeroes remaining_qty during processing (state-
    # inconsistency edge case). Per operator OQ-2 2026-05-08.
    ...
```

**`update_trade_fees_incremental` stays UNCONDITIONAL** (dispatcher.py:225-230) — entry fee from open fill must still be recorded. Verified by operator.

## Test fixes + new regression

1. **Fix existing `test_process_open_fill_orders_lookup_to_open_branch`** (test_dispatcher.py:366-386):
   - Change `select_position_state.return_value = _ps_row(remaining_qty=Decimal("5"))` to realistic value `_ps_row(remaining_qty=Decimal("0.001"))` (matching the open fill qty).
   - Assert `update_position_state_after_fill.assert_not_called()` (the fix's regression guard).
   - Assert `reconcile_close.assert_not_called()` (close-trigger does NOT fire).
   - Existing `insert_execution.kwargs["exec_type"] == "open"` assertion preserved.

2. **NEW `test_process_open_fill_does_not_decrement_remaining_qty`** — explicit regression guard:
   - Setup: order_match=100; trade.open_order_id=100 (open match); ps row remaining_qty=Decimal("0.001").
   - Assert `update_position_state_after_fill` NOT called.
   - Assert `update_trade_fees_incremental` IS called (entry fee recorded).
   - Assert `reconcile_close` NOT called.
   - Assert `insert_execution.kwargs["exec_type"] == "open"`.
   - Docstring: "H-030 regression guard: open fill must NOT decrement remaining_qty (placement-tx already wrote full qty)."

## H-030 hazard entry

Add after H-026 in BRIEF §20:

```
### H-030 — Open-fill must not decrement remaining_qty

**Context.** ExecutionDispatcher subtracts qty_delta from position_state.remaining_qty
on EVERY execution event. Placement tx writes remaining_qty=request.qty at trade-open
commit. If the dispatcher unconditionally subtracts qty for the open-fill audit event
(the WS execution event for the same fill), remaining_qty drops to 0 → triggers
close-flow → trade marked closed in DB while position is open on exchange.

**Policy.** Dispatcher MUST skip update_position_state_after_fill when exec_type="open".
Open-fill is already accounted-for at placement-tx time; the WS execution event is
audit-side only. update_trade_fees_incremental is UNCONDITIONAL (entry fee recorded
on every fill including open).

**Defensive guard.** Close-trigger check `if remaining_qty == 0` MUST also gate on
exec_type != "open" to protect against state-inconsistency edge cases where other
paths zero remaining_qty during open-fill processing.

**Test.** test_process_open_fill_does_not_decrement_remaining_qty + restored
test_process_open_fill_orders_lookup_to_open_branch with realistic remaining_qty.
```

## L-017 lesson

```
## L-017 (fix(T-218b), operator-discovered shipped-code bug, 2026-05-08)
Pattern: Test fixtures using artificial "post-update" mock values to bypass downstream
state-checks can hide bugs in the pre-update logic. test_process_open_fill_orders_lookup_to_open_branch
used `_ps_row(remaining_qty=Decimal("5"))` with comment "post-update non-zero (no close trigger)"
which assumed update_position_state_after_fill ran and decremented qty. The fixture
value was artificial — realistic placement-time pre-update value would be the FULL qty,
not 5. The test asserted reconcile_close.assert_not_called() but did NOT assert
update_position_state_after_fill.assert_not_called() — so the actual subtract bug
was masked behind the "post-update fake".
Active control: For state-mutation tests on functions that read state THEN mutate it,
test fixtures MUST use REALISTIC pre-mutation entering values (e.g. placement-time
remaining_qty = full qty, NOT artificial "post-update" placeholders). Test assertions
MUST pin BOTH the mutation behavior (update_X.assert_called_once / assert_not_called
explicitly) AND the post-condition (reconcile_close not called / row state matches
expected). Plan-reviewer Gate 1 MUST flag any test fixture with comments like
"post-update non-zero" or "artificial value to bypass X" without an explicit
assertion pinning the SHOULD-NOT-BE-CALLED helper.
```

## Hazards relevant from §20

- **H-030 NEW**: this fix's binding hazard.
- **H-016 (shadow task cleanup)**: open-fill spurious close would have fired `trade.closed.<bot_id>` → `ShadowWorker._on_parent_close` cancel hook → variants spurious-cancelled. H-030 fix prevents this propagation.
- **H-024 v2 (fill label derives from order ID)**: T-218b shipped this contract; H-030 fix preserves the contract for `exec_type="open"` (no functional change to derivation; only post-derivation state mutation skipped).
- **H-001 (cumulative-delta P&L)**: H-030 fix prevents phantom close events that would have polluted T-220 audit-loop trade_pnl_deltas.

## §N invariants

- **§N1 UTC**: no datetime changes.
- **§N3 idempotency**: dispatcher methods stay idempotent; conditional skip preserves idempotency.
- **§N4 TDD**: new regression test pins the bug; fix follows test → then code.
- **§N5 coverage**: dispatcher.py paths +1 conditional branch; tests cover both branches.
- **§N6 DI**: no new global state.
- **§N9 configurability**: no new Settings.

## L-008 / L-013 / L-014 / L-015

- L-008: no SQL literal Python type names.
- L-013: no JSONB writes.
- L-014: not applicable (small surgical fix, not FSM-scale task).
- L-015: no migration; sibling test impact = none.

## LOC budget

- dispatcher.py: ~12 LOC delta (conditional + guard + comment).
- test_dispatcher.py: ~80 LOC delta (existing fix + 1 new test).
- BRIEF §20: ~20 LOC (H-030 entry).
- review-lessons.md: ~10 LOC (L-017).
- plan-doc: ~150 LOC.

**Total**: ~12 LOC src + ~80 LOC tests + ~30 LOC docs + plan = ~272 LOC. **Far under §0.3 cap**.

## Acceptance criteria

1. `services/execution/app/dispatcher.py:213` wraps `update_position_state_after_fill` in `if exec_type != "open":` conditional.
2. Defensive close-trigger guard at dispatcher.py:237 — `if exec_type != "open" and ps_after is not None and ps_after.remaining_qty == Decimal("0"):`.
3. `update_trade_fees_incremental` (dispatcher.py:225-230) UNCHANGED — outside the conditional.
4. `insert_execution` (dispatcher.py:198-211) UNCHANGED — open-fill audit row still written.
5. Existing `test_process_open_fill_orders_lookup_to_open_branch` updated: realistic remaining_qty=Decimal("0.001"); assert update_position_state_after_fill NOT called; assert reconcile_close NOT called.
6. NEW `test_process_open_fill_does_not_decrement_remaining_qty` regression guard test.
7. NEW H-030 entry in BRIEF §20 (after H-026).
8. NEW L-017 lesson appended to docs/review-lessons.md.
9. Repo baseline 2088 → 2089 (+1 net new test; -0 + 1 = +1 since existing test is updated not removed).
10. All review gates passed: plan-reviewer APPROVE → drift-checker ON TRACK → brief-reviewer SHIP → math-validator VERIFIED (services/execution/ in scope; verification of "no math change; only conditional skip; remaining_qty arithmetic preserved for non-open paths").
11. Commit message: `fix(T-218b-open-fill-qty-bug): open-fill must not decrement remaining_qty (H-030)`.
12. Single fix commit; chore commit follows for TASKS.md + status.md + L-017 + H-030.
13. ci-full GREEN post-merge (would have caught nothing today since tests are new; existing 2088 still pass).

## Open questions

All operator decisions baked from session 2026-05-08:

- **OQ-1 [BAKED]**: Bug analysis approved.
- **OQ-2 [BAKED]**: Defensive close-trigger guard added (operator-suggested).
- **OQ-3 [BAKED]**: Patch as `fix(T-218b-open-fill-qty-bug)` urgent-fix on master.
- **OQ-4 [DEFAULT, no operator follow-up needed]**: H-030 reserved (NOT H-027/H-028/H-029 which are anticipated for T-525/T-534/T-535 per ADR-0011).
- **OQ-5 [DEFAULT, no operator follow-up needed]**: NEW L-017 lesson appended (test fixtures using artificial post-update values mask pre-update bugs).
- **OQ-6 [DEFAULT, no operator follow-up needed]**: PaperExchange open-fill stream verification deferred (paper mode bug-dormant per analysis; T-516a2/T-525 plan stages may revisit).

## Hand verification (math sanity)

Per math-validator gate 4 (services/execution/ touched):

**Pre-fix arithmetic** (BUG):
```
Placement tx commits: remaining_qty = qty (e.g. Decimal("0.001"))
WS open fill arrives: message.qty = Decimal("0.001")
update_position_state_after_fill subtracts: remaining_qty = 0.001 - 0.001 = 0
Close-trigger fires: remaining_qty == 0 → reconcile_close marks trade closed.
```

**Post-fix arithmetic** (CORRECT):
```
Placement tx commits: remaining_qty = qty (e.g. Decimal("0.001"))
WS open fill arrives: message.qty = Decimal("0.001")
exec_type = "open" → skip update_position_state_after_fill.
remaining_qty unchanged: 0.001.
Close-trigger does NOT fire (defensive guard also: exec_type != "open").
```

**Subsequent partial_tp / sl / trail fills** (UNCHANGED):
```
exec_type = "partial_tp" → update_position_state_after_fill subtracts qty_delta.
remaining_qty = 0.001 - tp_qty.
[continues per existing logic]
```

**Close fill** (UNCHANGED):
```
exec_type = "close" → update_position_state_after_fill subtracts full close qty.
remaining_qty = X - X = 0 → close-trigger fires (correctly this time).
```

No math changes; only conditional skip on the "open" branch. Decimal preservation preserved.

## Sibling migration test impact (L-015)

N/A — no migration.

## Write-time guidance (plan-reviewer APPROVE single-pass, 2026-05-08; verbatim)

1. **dispatcher.py:213-223 fix shape verbatim**: `if exec_type != "open":` wraps BOTH `new_sl_type` ternary (lines 213-215) AND `update_position_state_after_fill(...)` call (lines 216-223). `insert_execution` (lines 198-211) STAYS OUTSIDE conditional. `update_trade_fees_incremental` (lines 225-230) STAYS OUTSIDE conditional. Comment block above conditional must cite `T-218b fix(open-fill-qty-bug)` + reference placement_persist.py:419 + reason "audit-side mirror; placement tx already wrote remaining_qty".

2. **dispatcher.py:237 close-trigger guard**: existing line is `if ps_after is not None and ps_after.remaining_qty == Decimal("0"):` followed by raise + reconcile_close block (lines 238-258). Change is to PREPEND `exec_type != "open" and` to the existing guard — body lines 238-258 STAY UNCHANGED. Plan-doc §"The fix" second code block uses `...` as ellipsis for existing body; hlavný Claude Code MUST NOT replace existing body with ellipsis. Add 3-line comment above guard: `# H-030 defensive: open-fill must NEVER trigger close-flow even if some other path zeroes remaining_qty during processing (state-inconsistency edge case). Per operator OQ-2 2026-05-08.`

3. **Test factory match for new regression test**: `_execution_event_v(exchange_exec_id="exec-open")` default `qty=Decimal("5")`. Existing test `test_process_open_fill_orders_lookup_to_open_branch` uses default factory. New test `test_process_open_fill_does_not_decrement_remaining_qty` MUST keep ps_row pre-fill `remaining_qty=Decimal("5")` (matching event qty) so the "would have zeroed" condition is realistic. Plan-doc §"Test fixes" line 104 mentions `Decimal("0.001")` from a different sample — for THIS factory it must match the factory default `Decimal("5")` to remain hand-computable: pre-fill 5 - event_qty 5 = 0 (would-trigger-close pre-fix; skipped post-fix).

4. **Assertion pinning per L-017**: BOTH tests (existing-fix + new-regression) MUST contain explicit `patched_queries["update_position_state_after_fill"].assert_not_called()` AND `patched_queries["reconcile_close"].assert_not_called()` AND `patched_queries["insert_execution"].call_args.kwargs["exec_type"] == "open"` AND `patched_queries["update_trade_fees_incremental"].assert_called_once()` (entry fee recorded). All 4 assertions on each test, no exceptions.

5. **L-017 lesson append discipline**: `docs/review-lessons.md` — L-017 is the NEXT slot. Existing tail per current file is L-016 (T-512a). L-014/L-015 already in the file (out-of-order numbering preserved). L-017 entry uses verbatim plan-doc §"L-017 lesson" text + appended-with-line-breaks per file convention. NOT inserted in numerical order — appended at file tail.

6. **H-030 BRIEF §20 placement**: NEW H-030 entry inserted after H-026 in BRIEF §20 (file ends with H-026 at line 2814 per current state). Section §21 Glossary starts at 2818. Insert H-030 block between line 2815 (end of H-026 block) and line 2816 (separator) so existing `---` separator + §21 header stay intact. Verbatim text per plan-doc §"H-030 hazard entry".

7. **TASKS.md late-night X section + L-017 + H-030 in chore commit**: split into 2 commits: (a) `fix(T-218b-open-fill-qty-bug): ...` containing src + tests ONLY; (b) `chore(tasks): ...` containing TASKS.md + status.md + L-017 + H-030 — mirror fix(T-511b2a) pattern. Per CLAUDE.md commit-format: NO trailer; `-m` per paragraph.

8. **PaperExchange open-fill stream verification deferred**: do NOT touch `services/execution/app/paper.py` / `paper_persistence.py` at fix-time. Plan §Out-of-scope explicit. If during implementation a related concern surfaces (PaperExchange path also broken), STOP and escalate to operator for OQ-7 — do NOT silently expand scope.

9. **Math-validator gate-4 hand-verification**: §"Hand verification" already in plan covers arithmetic for pre-fix (BUG) + post-fix (CORRECT) + subsequent partial_tp/sl/trail (UNCHANGED) + close (UNCHANGED). Math-validator MUST cross-check that test fixtures use Decimal throughout (no Float / float coercion); no `Decimal(<float-literal>)`; no `getcontext().prec` mutations. The fix is conditional-skip only — no new arithmetic — so math-validator should VERIFY quickly.

10. **AC#9 baseline number**: plan claims 2088 → 2089. Hlavný Claude Code MUST run `pytest --co -q | tail -3` BEFORE implementing to capture actual current baseline; AC#9 line in commit message references the actual measured number, not the plan's preliminary estimate. Drift between estimate and reality is acceptable; load-bearing for review is +1 net new test, not absolute count.
