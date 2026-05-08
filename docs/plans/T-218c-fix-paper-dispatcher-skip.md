# fix(T-218c-paper-dispatcher-skip) — Paper adapter must not feed live ExecutionDispatcher (H-031)

**Phase:** F5 (urgent fix; mirror fix(T-218b) precedent — does NOT count toward F5 numbered task counter).
**Type:** Critical bug fix; pre-live blocker.
**Hazards bound:** H-031 NEW (paper adapter must not feed live ExecutionDispatcher).
**Related**: fix(T-218b) shipped 2026-05-08 (H-030 open-fill subtract bug; LIVE-mode protected); T-218b plan claimed "bug dormant in paper mode" — that claim was INCORRECT (paper has separate kill-path addressed here).

## Bug analysis (operator-discovered + verified 2026-05-08)

**Root cause**: ExecutionDispatcher tasks are created per-bot in main.py:215-240 for ALL adapters (including paper). PaperExchange emits ExecutionEvent via `_execution_queue` (paper/adapter.py:820-831 inside `_persist_open`; line 930 inside `_persist_close`; line 1185 in `_emit_close_events`). `stream_executions` async iterator consumes the queue (paper/adapter.py:1338-1354). Dispatcher's `consume()` → `_process()` runs against LIVE tables (`orders` / `trades` / `position_state` / `executions`).

**Reproduction chain**:
1. Paper bot signal → strategy-engine publishes OrderRequest.
2. Per-bot orders.requests subscriber receives → `placement.py` paper-fork (line 240-252) calls `adapter.place_market_order(...)`.
3. `PaperExchange._persist_open` (paper/adapter.py:786+) writes `paper_orders` + `paper_trades` + `paper_executions` + `paper_positions` (single tx); enqueues `ExecutionEvent` to `_execution_queue` (line 820-831).
4. `run_dispatcher_for_bot` is iterating `adapter.stream_executions()`; receives the event.
5. `ExecutionDispatcher._process` runs:
   - `select_order_id_by_exchange_id(message.exchange_order_id)` — paper uses `exchange_order_id` like `paper-uuid8`; live `orders` table has no such row → returns `None`.
   - `_derive_exec_type` → `select_position_state(bot_id, symbol)` — paper bots write to `paper_positions` (NOT live `position_state`); placement-tx for paper takes the early-return paper-fork BEFORE reaching `placement_persist.py:419 insert_position_state`. So live position_state has no row → `_derive_exec_type` returns `("unknown", None, None)`.
   - dispatcher.py:188-196 raises `RuntimeError("unattributable fill: no order match and no position_state")`.
6. `run_dispatcher_for_bot:322-326` re-raises after logging `execution.dispatcher_stream_terminated`.
7. Task dies. main.py:392 `gather(*dispatcher_tasks, return_exceptions=True)` swallows pri shutdown — operator sees nothing during runtime.

**Real-world impact**:
- Every paper bot's first open-fill kills its ExecutionDispatcher task SILENTLY.
- Subsequent paper fills not consumed by dispatcher (queue accumulates; `_execution_queue` bounded at maxsize 1000 per Decision #11 → eventually back-pressures or drops).
- Paper bot's open / close flow ITSELF still works (PaperExchange handles persist + emit internally; dispatcher's role is post-hoc audit + state mutation on LIVE tables, irrelevant for paper).
- T-220b P&L audit loop (cumulative-delta) operates on `trades` / `paper_trades` separately so paper bot's trade lifecycle audit is intact.
- BUT: any future feature relying on dispatcher consumption (e.g., dispatcher-driven event publishing, lifecycle FSM ticks dependent on dispatcher) would break for paper.
- More importantly: a dead task is OPERATIONAL DEBT — masks issues + prevents correct re-spawn semantics.

**T-218b retrospective**: T-218b plan claimed "bug dormant in paper mode (PaperExchange synthetic-fill flow does NOT emit ExecutionEvent for open fills via stream_executions; only SL/TP synthetic fills emit)". This claim was INCORRECT — verified by reading paper/adapter.py:820-831 (`_persist_open` enqueues ExecutionEvent on OPEN). T-218b H-030 fix protected LIVE mode only; PAPER mode has separate kill-path (unattributable_fill RuntimeError) which fires BEFORE the H-030 conditional even reaches the `update_position_state_after_fill` call site. Therefore both fixes are needed: H-030 (T-218b) for live + H-031 (T-218c) for paper.

## Operator decisions baked (session 2026-05-08)

- **OQ-1 [BAKED]**: Fix order = Item 1 first (paper dispatcher kill).
- **OQ-2 [BAKED]**: Skip dispatcher creation for paper adapters (NOT make dispatcher table-aware via `paper_*` routing — bigger scope; operator chose simplest fix).
- **OQ-3 [BAKED]**: T-218b retrospective correction in T-218c chore commit (operator-confirmed).
- **OQ-4 [DEFAULT, no operator follow-up needed]**: H-031 reserved as next available H-NNN slot (H-027/H-028/H-029 reserved for ADR-0011 anticipated; H-030 = T-218b shipped; H-031 = T-218c this).
- **OQ-5 [DEFAULT, no operator follow-up needed]**: NEW L-018 lesson — "plan analysis claiming 'bug dormant in X mode' is itself a hazard — verify by reading code, not by reasoning". Active control: plan-reviewer Gate 1 must demand explicit code-citation evidence (file:line + grep) for any "dormant in mode" claim, NOT reasoning.

## Scope

**In scope:**
- `services/execution/app/pool.py` — extend `AdapterPoolResult` with `paper_bot_ids: frozenset[BotId]` field (NEW; populated during `build_adapter_pool` for `bot_row.exchange_mode == "paper"`).
- `services/execution/app/main.py` — wrap dispatcher task creation block (line 215-240) with `if bot_id in adapter_pool.paper_bot_ids: continue` skip; orders.requests subscribe (line 166) STAYS for paper bots (placement handler still receives OrderRequests for paper).
- `services/execution/tests/test_pool.py` — extend test to verify `paper_bot_ids` populated correctly.
- `services/execution/tests/test_app_factory.py` — NEW test asserts no `dispatcher_<paper_bot_id>` task created for paper bots.
- `docs/CLAUDE_CODE_BRIEF.md` §20 — NEW H-031 hazard entry (after H-030; H-027/H-028/H-029 reserved for ADR-0011 anticipated).
- `docs/review-lessons.md` — NEW L-018 lesson.
- TASKS.md — append T-218b retrospective correction note + new fix(T-218c) DONE entry at top of Done.
- docs/status.md — late-night XI section.

**Out of scope:**
- Make dispatcher table-aware of paper_* tables (alternative architecture; deferred per operator OQ-2).
- Other 6 audit items (Items 2-7); separately tracked per operator session 2026-05-08 triage.

## Files touched

```
services/execution/app/pool.py                                   M  (~10 LOC; +paper_bot_ids field + populate)
services/execution/app/main.py                                   M  (~3 LOC; +skip continue)
services/execution/tests/test_pool.py                            M  (~30 LOC; +paper_bot_ids assertion)
services/execution/tests/test_app_factory.py                     M  (~50 LOC; +regression test)
docs/CLAUDE_CODE_BRIEF.md                                        M  (~25 LOC; H-031 hazard entry after H-030)
docs/review-lessons.md                                           M  (~12 LOC; L-018 lesson)
docs/plans/T-218c-fix-paper-dispatcher-skip.md                   NEW (this plan; ~200 LOC)
TASKS.md                                                         M  (chore; T-218c DONE entry + T-218b retrospective)
docs/status.md                                                   M  (chore; late-night XI section)
```

## The fix

**pool.py** — extend AdapterPoolResult:

```python
@dataclass(frozen=True, slots=True)
class AdapterPoolResult:
    adapters: dict[BotId, ExchangeClient]
    ws_tasks: list[asyncio.Task[None]]
    paper_consumer_tasks: list[asyncio.Task[None]]
    paper_bot_ids: frozenset[BotId]  # NEW T-218c H-031
```

**pool.py** — populate during build:

```python
async def build_adapter_pool(...) -> AdapterPoolResult:
    ...
    paper_bot_ids: set[BotId] = set()  # NEW T-218c H-031
    for bot_row in bot_rows:
        if bot_row.exchange_mode in ("live", "testnet"):
            adapter, ws_task = _construct_bybit_adapter(...)
            adapters[BotId(bot_row.bot_id)] = adapter
            ws_tasks.append(ws_task)
        else:
            paper_adapter, consumer_task = _construct_paper_adapter(...)
            adapters[BotId(bot_row.bot_id)] = paper_adapter
            paper_consumer_tasks.append(consumer_task)
            paper_bot_ids.add(BotId(bot_row.bot_id))  # NEW T-218c H-031
    ...
    return AdapterPoolResult(
        adapters=adapters,
        ws_tasks=ws_tasks,
        paper_consumer_tasks=paper_consumer_tasks,
        paper_bot_ids=frozenset(paper_bot_ids),  # NEW
    )
```

**main.py** — skip dispatcher creation for paper:

```python
# 6. Per-bot ExecutionDispatcher tasks (T-218a; H-009 per-bot dedup ring).
# T-218c fix(paper-dispatcher-skip) / H-031: skip for paper bots —
# PaperExchange has its own internal persist + audit pipeline via
# paper_orders / paper_trades / paper_executions / paper_positions tables;
# the live ExecutionDispatcher tries to look up exchange events in LIVE
# tables (orders / trades / position_state) which paper bots don't write
# to → unattributable_fill RuntimeError → task dies silently. Per
# operator OQ-2 2026-05-08: simplest fix is skip; alternative table-
# aware dispatcher routing deferred. Per ADR-0011 paper-mode is primary
# operator mode; this fix is pre-live blocker.
dispatcher_tasks: list[asyncio.Task[None]] = []
for bot_id, adapter in adapter_pool.adapters.items():
    if bot_id in adapter_pool.paper_bot_ids:
        continue
    sub_account = _resolve_sub_account(adapter)
    ...
```

**Note**: orders.requests subscribe loop at main.py:166 STAYS for ALL bots (including paper) — placement handler routes paper orders via PaperExchange.place_market_order; that path is independent of ExecutionDispatcher.

## Tests

1. **NEW `test_build_adapter_pool_populates_paper_bot_ids`** in test_pool.py — given mixed live + paper bots, assert `result.paper_bot_ids` == `frozenset{paper_bot_ids}` and live bots NOT in set.
2. **NEW `test_lifespan_does_not_create_dispatcher_task_for_paper_bots`** in test_app_factory.py — patch `build_adapter_pool` to return result with mixed live + paper; assert `dispatcher_tasks` length matches live-only count + no `dispatcher_<paper_bot_id>` task names exist.
3. **MODIFY** existing `test_lifespan_invokes_resume_active_observations_after_rejected_worker_start_when_enabled` (or similar lifespan tests if affected by AdapterPoolResult shape change) — verify they still work with the new field (frozenset default).

## H-031 hazard entry (BRIEF §20, after H-030)

```
### H-031 — Paper adapter must not feed live ExecutionDispatcher

**Context.** ExecutionDispatcher consumes adapter.stream_executions() per-bot
and processes events via LIVE tables (orders / trades / position_state).
PaperExchange writes to paper_* tables and emits ExecutionEvent for both open
and close (paper/adapter.py:820 _persist_open + :930 + :1185). Dispatcher's
LIVE table lookups return None for paper events → unattributable_fill
RuntimeError → run_dispatcher_for_bot re-raises → task dies silently
(lifespan gather return_exceptions=True swallows pri shutdown only).

**Policy.** ExecutionDispatcher tasks MUST NOT be created for adapters whose
bot_row.exchange_mode == "paper". Paper bots have an internal pipeline via
PaperExchange (persist to paper_* + emit events via stream_executions for
event-shape symmetry); the LIVE dispatcher's role is irrelevant for paper.
AdapterPoolResult.paper_bot_ids set is the canonical source for the skip.

**Test.** test_lifespan_does_not_create_dispatcher_task_for_paper_bots +
test_build_adapter_pool_populates_paper_bot_ids.

H-numbering note: T-218b shipped H-030 (open-fill must not decrement
remaining_qty) on 2026-05-08 LIVE-mode-protective; T-218c addresses the
PAPER-mode separate kill-path. T-218b plan claim "bug dormant in paper
mode" was incorrect — paper had its own (different) crash path documented
here.
```

## L-018 lesson

```
## L-018 (fix(T-218c-paper-dispatcher-skip), operator-discovered shipped-code bug, 2026-05-08)
Pattern: Plan analysis claiming "bug dormant in <mode>" is itself a hazard if not backed by direct code-reading evidence. fix(T-218b) plan claimed "PaperExchange synthetic-fill flow does NOT emit ExecutionEvent for open fills via stream_executions (only SL/TP synthetic fills emit)" — this was reasoning, NOT verified evidence. Direct reading of paper/adapter.py:820-831 shows _persist_open enqueues ExecutionEvent on every open fill. The plan-doc's incorrect claim led to T-218b being framed as "live-mode-only" while paper mode had its own separate kill-path (dispatcher.py:188 unattributable_fill RuntimeError) which would fire BEFORE H-030 conditional even reached. The H-030 fix is correct for live; H-031 (T-218c) addresses paper independently.
Active control: For any plan claim of the shape "bug dormant in <mode>" or "<mode> not affected by this issue", plan-reviewer Gate 1 MUST require explicit code citation (file:line + grep evidence) showing the relevant code path is not exercised. Reasoning-only claims must be flagged as BLOCKER until code-verified. Brief-reviewer Gate 3 + math-validator Gate 4 should similarly verify any "X is dormant" claim against grep evidence at commit time. Operator-driven audit catches such claims; review system should catch them earlier at plan time.
```

## Hazards relevant from §20

- **H-031 NEW**: this fix's binding hazard.
- **H-030 (T-218b shipped)**: companion sibling; T-218b protects live, T-218c protects paper from a DIFFERENT kill-path. Together complete dispatcher safety contract.
- **H-016 (shadow task cleanup)**: paper variants in shadow runtime — ShadowWorker's per-variant task uses its own per-variant PaperExchange instance; not affected by primary-bot dispatcher skip.

## §N invariants

- §N1 UTC: no datetime changes.
- §N3 idempotency: no idempotency markers changed.
- §N5 coverage: +2 net new tests (pool + app_factory regression).
- §N6 DI: AdapterPoolResult dataclass field addition; no global state.
- §N9 configurability: no new Settings.

## L-008 / L-013 / L-014 / L-015 / L-017

- L-008: no SQL literals.
- L-013: no JSONB writes.
- L-014: not applicable (small surgical fix).
- L-015: no migration; sibling test impact = none.
- L-017: state-mutation-test discipline — N/A (no state mutation in fix; adapter pool result construction is read-only field add).

## LOC budget

- pool.py delta: ~10 LOC (1 field + populate logic).
- main.py delta: ~3 LOC (skip continue).
- test_pool.py delta: ~30 LOC (1 new test).
- test_app_factory.py delta: ~50 LOC (1 new regression test).
- BRIEF §20 H-031 entry: ~25 LOC.
- L-018 entry: ~12 LOC.
- TASKS.md + status.md: chore commit.
- plan-doc: ~200 LOC.

**Total**: ~13 LOC src + ~80 LOC tests + ~37 LOC docs + plan = ~330 LOC. **Far under §0.3 cap**.

## Acceptance criteria

1. `AdapterPoolResult.paper_bot_ids: frozenset[BotId]` field added (NEW).
2. `build_adapter_pool` populates `paper_bot_ids` for `bot_row.exchange_mode == "paper"`.
3. main.py:215-240 dispatcher creation block skips paper bots via `if bot_id in adapter_pool.paper_bot_ids: continue`.
4. orders.requests subscribe loop at main.py:166 UNCHANGED — paper bots still receive OrderRequests via subscriber.
5. NEW `test_build_adapter_pool_populates_paper_bot_ids` in test_pool.py verifies frozenset content for mixed-mode bot rows.
6. NEW `test_lifespan_does_not_create_dispatcher_task_for_paper_bots` in test_app_factory.py asserts no `dispatcher_<paper_bot_id>` task created.
7. NEW H-031 entry in BRIEF §20 (after H-030 entry).
8. NEW L-018 lesson appended to docs/review-lessons.md.
9. TASKS.md fix(T-218c) DONE entry + T-218b retrospective correction note appended (per operator OQ-3).
10. docs/status.md late-night XI section.
11. Repo baseline 2089 → 2091 (+2 net new tests).
12. All 4 review gates passed: plan-reviewer APPROVE → drift-checker ON TRACK → brief-reviewer SHIP → math-validator VERIFIED out-of-scope (services/execution/ touched but NO math change; AdapterPoolResult field add is composition not arithmetic).
13. Commit message: `fix(T-218c-paper-dispatcher-skip): paper adapter must not feed live ExecutionDispatcher (H-031)`.
14. Single fix commit (src + tests) + chore commit (TASKS + status + BRIEF + lesson + plan-doc + retrospective).

## Open questions

All operator decisions baked from session 2026-05-08:

- **OQ-1 [BAKED]**: Fix order = Item 1 first.
- **OQ-2 [BAKED]**: Skip dispatcher (not table-aware routing).
- **OQ-3 [BAKED]**: T-218b retrospective in chore commit.
- **OQ-4 [DEFAULT, no operator follow-up needed]**: H-031 reserved.
- **OQ-5 [DEFAULT, no operator follow-up needed]**: NEW L-018 lesson on "dormant in mode" claim hazard.

## Hand verification (math sanity)

N/A — no arithmetic change; AdapterPoolResult is dataclass field addition (composition, not math). Skip-conditional in main.py is set-membership check (`bot_id in frozenset`). Math-validator should return VERIFIED — out-of-scope OR VERIFIED with the trivial assertion that no Decimal arithmetic touched.

## Sibling migration test impact (L-015)

N/A — no migration.

## Write-time guidance (plan-reviewer APPROVE single-pass, 2026-05-08; verbatim)

1. **conftest.py:102 `mock_adapter_pool_result` fixture** — add `result.paper_bot_ids = frozenset()` next to existing `paper_consumer_tasks = []` default. Without this, EVERY test using `app_with_mocks` (tests routed through conftest fixture path) hits new `if bot_id in adapter_pool.paper_bot_ids` check and raises `TypeError: argument of type 'MagicMock' is not iterable`.

2. **test_app_factory.py — 13 `fake_pool_result` construction sites** — every block setting `fake_pool_result.adapters = {...}` + `fake_pool_result.paper_consumer_tasks = []` (lines 124-126, 183-185, 257-259, 315-317, 384-386, 449-451, 524-526, 606-608, 681-683, 743-745, 819-821, 882-884, 939-941) MUST also set `fake_pool_result.paper_bot_ids = frozenset()`. Skipping any site causes the dispatcher creation loop iteration to raise TypeError on that test's lifespan startup. NEW positive test (`test_lifespan_does_not_create_dispatcher_task_for_paper_bots`) is the only site that sets a non-empty frozenset.

3. **NEW positive test** — `test_lifespan_does_not_create_dispatcher_task_for_paper_bots`: assert `dispatcher_<paper_bot_id>` NOT in `[t.get_name() for t in dispatcher_tasks]` (or via `app.state.dispatcher_tasks` if exposed). ALSO assert `orders.requests.<paper_bot_id>` IS in subscribe_subjects (placement subscriber preserved — guard against accidental over-skip).

4. **pool.py field ordering** — `paper_bot_ids: frozenset[BotId]` field is added after `paper_consumer_tasks` per plan; `frozen=True, slots=True` dataclass — keyword-only construction at line 271 (`return AdapterPoolResult(adapters=..., ws_tasks=..., paper_consumer_tasks=..., paper_bot_ids=...)`) preserves positional-stability for existing callers. Test mocks use `MagicMock` (attribute injection, not positional); production callers use kwargs.

5. **TASKS.md T-218b retrospective correction (per OQ-3)** — append to existing T-218b DONE entry the correction note: *"Plan claim 'bug dormant in paper mode (PaperExchange synthetic-fill flow does NOT emit ExecutionEvent for open fills via stream_executions; only SL/TP synthetic fills emit)' was INCORRECT — verified 2026-05-08 by reading paper/adapter.py:820-831. Paper had separate kill-path (unattributable_fill RuntimeError at dispatcher.py:188) which fires BEFORE H-030 conditional reaches. T-218b H-030 fix is correct for live; T-218c H-031 addresses paper independently. See L-018."*

6. **L-018 lesson at end of review-lessons.md** — append after L-017 (currently the last entry); preserve numbered ordering. Active control mandates explicit code citation (file:line + grep) for ALL "X dormant in Y mode" claims at plan-reviewer Gate 1; brief-reviewer Gate 3 must also verify against grep at commit time.

7. **Commit shape** — single fix commit (pool.py + main.py + conftest.py + test_pool.py + test_app_factory.py) + chore commit (TASKS.md + status.md + BRIEF §20 + review-lessons.md + plan-doc). Plan AC-14 already specifies this split. No --amend; new commit per CLAUDE.md non-negotiable.
