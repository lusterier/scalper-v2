# fix(T-216c-fill-price-retry-exception) — placement get_fill_price retry must catch transient exceptions (H-032)

**Type**: surgical bug fix (mirror T-218b/T-218c pattern; not a new F5 numbered task; F5 phase counter UNCHANGED).
**Phase**: F5 (unlocked).
**Origin**: operator-discovered shipped-code bug 2026-05-08; Item 5 of 7-bug audit (per `docs/status.md` late-night XI 7-bug audit progress tracker).
**Date**: 2026-05-09.

## Bug

`services/execution/app/placement.py:205-232` retry loop iterates over `adapter.get_fill_price(symbol, exchange_order_id)` and treats `None` returns as "no fill yet, retry after backoff". When `get_fill_price` **raises** an exception, the retry loop's `await` site has NO try/except — the exception propagates UP and OUT of the loop without:

1. The retry counter advancing (transient timeout = immediate fail, no second try).
2. The `await asyncio.sleep(fill_price_retry_backoff_s)` running.
3. The `if fill_price is None:` block being reached → no `execution.fill_price_unresolved` ERROR log.
4. The `bus.publish(subject_for_orders_dlq(bot_id), envelope)` DLQ publish.
5. The `raise FillPriceUnresolvedError(...)` explicit error contract being preserved.

Instead the exception (typically `NetworkTimeout` / `RateLimitError` / `AuthError` from `bybit_v5/adapter.py:273-296` `_v5/execution/list` HTTP request; or asyncpg errors from `paper/adapter.py:1299-1309`) propagates up out of `_handle` into `OrderRequestDedupConsumer.consume` and then into `bus.subscribe()`'s framework-level catch — typically silently swallowed at the bus level with minimal context (no fill-price-specific diagnostics, no DLQ artifact).

Sibling steps in the same handler **DO** wrap their adapter calls with explicit try/except matching `§11.3` error taxonomy:

- step 4 (`set_leverage`, line 159-168): `(AuthError, OrderRejected, NetworkTimeout, RateLimitError)`
- step 5 (`place_market_order`, line 170-204): `UnknownState` + `OrderRejected` + `(AuthError, NetworkTimeout, RateLimitError)`

Step 6 (`get_fill_price` retry block) was implemented as if `get_fill_price` only ever returned `None` or a `Decimal` — the exception path was forgotten. Existing tests cover only None-return retry semantics (`test_handler_returns_fill_price_on_first_attempt_when_not_none` + `test_handler_retries_up_to_3_times_when_fill_price_returns_none` + `test_fill_price_unresolved_after_all_None_attempts_publishes_to_dlq_and_raises` + `test_handler_dlq_publish_failure_still_raises_FillPriceUnresolvedError`); zero exception-path coverage.

### Real-world impact

- **Live**: Bybit `/v5/execution/list` is called immediately after `place_market_order` returns `exchange_order_id`. WS-event-vs-HTTP race, transient gateway latency, or REST 5xx → `NetworkTimeout`. Currently fatal: position OPENS on exchange (real money) but DB persistence aborts mid-handler (no trades row, no position_state row, no SLMoved emit). Reconciliation T-221 may eventually catch the orphan, but explicit DLQ artifact for operator visibility is bypassed.
- **Paper**: `select_paper_execution_price_by_order_id` reads from `paper_executions`; transient asyncpg errors (timeout on conn pool exhaustion, etc.) → similar fatal exit.
- **Either mode**: handler exception trips up out of consumer; bus-level swallow means no operator-facing trace of the specific transient failure.

## Why not surfaced earlier

- `bybit_v5/adapter.py:273-296` `get_fill_price` itself only catches `RateLimitError` (limiter cleanup) and re-raises all else — by design (taxonomy at adapter boundary). Caller is expected to handle.
- T-216a / T-216b1 / T-216b2 reviewer focus was the post-fill_price pipeline + paper fork + persistence-tx + emit ordering; the retry block's `await` site exception coverage slipped through both plan-reviewer and brief-reviewer Gate 3 because no `for attempt in range(*retry*)` audit pattern existed (now becomes L-019 active control).
- Operator's primary mode is paper per memory `deployment.md`; v2 multi-service NIE JE deployed; sibling v1 testnet stack disabled 2026-05-02; testnet smoke (T-222 F2 close-out) was never executed end-to-end. The kill-path was never observed at runtime.

## Operator decisions (2026-05-09 OQ session)

- **OQ-1 = A** Narrow trio: catch `(AuthError, NetworkTimeout, RateLimitError)` (mirror sibling step 5 catch verbatim; `UnknownState`/`OrderRejected` are not `get_fill_price` errors per §11.3).
- **OQ-2 = A** Treat-as-None retry semantics: exception → log warn + fill_price stays None → retry counter advances + sleep ak ostávajú attempts. Po vyčerpaní → existing FillPriceUnresolvedError + DLQ cesta. Symetria s None retry.
- **OQ-3 = A** NEW H-032 hazard entry in BRIEF §20 paired with H-030/H-031 dispatcher cluster.
- **OQ-4 = A** NEW L-019 review-lesson: retry loops over external calls must wrap the await-site with try/except matching the SAME error taxonomy as non-retried sibling calls in the same handler.

## Fix shape

`services/execution/app/placement.py:205-232` retry block replaced with try/except wrapping the `await adapter.get_fill_price(...)` call. Pseudo-code:

```python
# 6. get_fill_price with inline retry (Settings-tunable per L-001).
fill_price: Decimal | None = None
for attempt in range(fill_price_retry_attempts):
    try:
        fill_price = await adapter.get_fill_price(
            request.symbol,
            place_result.exchange_order_id,
        )
    except (AuthError, NetworkTimeout, RateLimitError) as exc:
        # T-216c / H-032: transient adapter error in retry loop must NOT bypass
        # the FillPriceUnresolvedError + DLQ contract. Treat as None + retry.
        logger.warning(
            "execution.get_fill_price_transient_error",
            bot_id=bot_id,
            exchange_order_id=place_result.exchange_order_id,
            attempt=attempt + 1,
            error=str(exc),
        )
        fill_price = None
    if fill_price is not None:
        break
    if attempt + 1 < fill_price_retry_attempts:
        await asyncio.sleep(fill_price_retry_backoff_s)
if fill_price is None:
    # ... existing block unchanged (DLQ publish + raise FillPriceUnresolvedError) ...
```

Net code delta: ~10 lines added (try/except + warn log + reset to None). No structural refactor; no new dataclass / function / Settings.

## Out of scope

- `get_fill_price` adapter implementations (`bybit_v5/adapter.py` + `paper/adapter.py`) untouched. Adapter taxonomy contract preserved.
- Other retry loops in the codebase (e.g. `bybit_v5/client.py` request-level retry) are at HTTP-layer with their own taxonomy; not in scope for T-216c.
- Distinct error log key for transient-exhausted vs None-exhausted at the `fill_price_unresolved` final log: KEEP existing single key (no operator-facing distinction needed; warn-log per attempt provides the granularity).
- Step 4 (`set_leverage`) retry: not currently retried; no change.
- Step 5 (`place_market_order`) retry: H-003 zero-retry by design; no change.

## Files touched

### Source

1. `services/execution/app/placement.py` — wrap `get_fill_price` await site with try/except; add warn log key `execution.get_fill_price_transient_error`. Also update the docstring's bullet "6. Call adapter.get_fill_price..." to mention transient-exception retry-as-None semantics.

### Tests

2. `services/execution/tests/test_placement.py` — 4 NEW regression tests in the existing "Fill-price retry" section:
   - `test_handler_retries_when_get_fill_price_raises_NetworkTimeout` — `side_effect=[NetworkTimeout("conn timeout"), Decimal("100.0")]`, attempts=3 → succeeds on attempt 2; assertion: `await_count == 2`, no exception raised, no DLQ publish, warn log `execution.get_fill_price_transient_error` count == 1.
   - `test_handler_retries_when_get_fill_price_raises_RateLimitError` — `side_effect=[RateLimitError("429"), RateLimitError("429"), Decimal("100.0")]` → succeeds on attempt 3; awaits == 3; warn count == 2.
   - `test_handler_retries_when_get_fill_price_raises_AuthError` — symmetric to above with `AuthError`; demonstrates trio-catch coverage.
   - `test_fill_price_unresolved_after_all_exception_attempts_publishes_to_dlq_and_raises` — `side_effect=[NetworkTimeout, NetworkTimeout, NetworkTimeout]`, attempts=3 → raises `FillPriceUnresolvedError`, awaits == 3, DLQ published once on `orders.dlq.alpha`, warn count == 3, error log `execution.fill_price_unresolved` present once.

   No existing tests modified — additive only (mirror T-218b L-017 pattern for state-mutation test pinning, but here it's pure-exception-path additions).

### Documentation

3. `docs/CLAUDE_CODE_BRIEF.md` — NEW H-032 hazard entry between H-031 and §21 Glossary (chore commit).
4. `docs/review-lessons.md` — NEW L-019 lesson appended after L-018 (chore commit).
5. `TASKS.md` — fix(T-216c) DONE entry at top of Done section; F5 phase counter UNCHANGED at 26/47 (chore commit).
6. `docs/status.md` — late-night XII section prepended above late-night XI; updates 7-bug audit progress tracker (Item 5 → DONE) (chore commit).
7. `docs/plans/T-216c-fix-fill-price-retry-exception.md` — this plan doc; staged in chore commit per CLAUDE.md gate-1 requirement.

## NEW H-032 hazard text (for BRIEF §20)

```
### H-032 — Retry loop over external adapter call must catch transient exceptions

**Context.** `services/execution/app/placement.py` step 6 calls `adapter.get_fill_price(symbol, order_id)` inside `for attempt in range(fill_price_retry_attempts)` retry loop. The `await` site originally had no try/except — when adapter raised `NetworkTimeout` / `RateLimitError` / `AuthError` from underlying HTTP call (Bybit `/v5/execution/list` per `bybit_v5/adapter.py:273-296`) or asyncpg errors (paper `select_paper_execution_price_by_order_id` per `paper/adapter.py:1299-1309`), the exception bypassed the retry counter, the `await asyncio.sleep(backoff)` step, AND the post-loop `if fill_price is None: DLQ + FillPriceUnresolvedError` contract. Exception propagated up to `bus.subscribe()` framework-level swallow with minimal operator-facing context. Operator-discovered shipped-code bug 2026-05-08; fix shipped via `fix(T-216c-fill-price-retry-exception)` precedent.

**Policy.** Any retry loop over an external adapter call MUST wrap the `await` site with try/except matching the same error taxonomy as non-retried sibling calls in the same handler. For `placement.py` get_fill_price block: `(AuthError, NetworkTimeout, RateLimitError)` mirroring step 5 `place_market_order` catch (per `§11.3` error taxonomy). Exception treated as None: warn-log + retry counter advances + sleep on remaining attempts + post-loop DLQ + `FillPriceUnresolvedError` contract preserved.

**Test.** `test_handler_retries_when_get_fill_price_raises_NetworkTimeout` + `test_handler_retries_when_get_fill_price_raises_RateLimitError` + `test_handler_retries_when_get_fill_price_raises_AuthError` + `test_fill_price_unresolved_after_all_exception_attempts_publishes_to_dlq_and_raises`.

H-032 numbering note: companion to H-030 (open-fill remaining_qty contract) + H-031 (paper adapter must not feed live ExecutionDispatcher). Together H-030/H-031/H-032 form the execution-service operational hardening cluster surfaced via operator audit 2026-05-08/05-09.
```

## NEW L-019 lesson text (for review-lessons.md)

```
## L-019 (fix(T-216c-fill-price-retry-exception), operator-discovered shipped-code bug, 2026-05-08/05-09)
Pattern: Retry loops over external adapter calls must wrap the `await` site with try/except matching the SAME error taxonomy as non-retried sibling calls in the same handler. `placement.py:205-232` retry block iterated `for attempt in range(fill_price_retry_attempts)` over `await adapter.get_fill_price(...)` with NO try/except — exceptions (`NetworkTimeout` / `RateLimitError` / `AuthError`) bypassed the retry counter AND the post-loop `FillPriceUnresolvedError + DLQ` contract. Sibling step 4 (set_leverage) + step 5 (place_market_order) DO have explicit `try/except (AuthError, OrderRejected, NetworkTimeout, RateLimitError)` blocks at their await sites — the retry block was written as if `get_fill_price` only ever returned None or Decimal, never raised. Existing tests covered only None-return retry; zero exception-path coverage. Reviewer T-216a / T-216b1 / T-216b2 focus was post-fill_price pipeline + paper fork + persist-tx + emit ordering — the retry block's exception coverage slipped through both plan-reviewer and brief-reviewer Gate 3 because no `for attempt in range(*retry*)` audit pattern existed.
Active control: For any plan / staged diff containing a retry loop pattern (`for ... in range(*retry*)` or equivalent counter-driven retry), plan-reviewer Gate 1 + brief-reviewer Gate 3 MUST grep the await sites inside the loop and verify they have explicit try/except matching either (a) the taxonomy of non-retried sibling calls in the same handler, or (b) the documented adapter contract for transient errors. ANY raw `await ext_call(...)` inside a retry loop without exception handling is a BLOCKER. The pattern generalizes: counter-based retry with no exception handling on the await site is a hidden silent-failure path.
```

## Hand verification

N/A — no financial math. Composition-only fix wraps an `await` site with try/except + warn log + None-reset. Math-validator Gate 4 expected `VERIFIED — out of scope, math-validator skipped` (mirror T-218c precedent: `services/execution/` touched but no Decimal/float arithmetic, no indicator/seed convention, no P&L computation).

## LOC budget

- src delta: ~10 LOC (try/except wrapper + warn-log call + None-reset + 1-line docstring update).
- test delta: ~70 LOC (4 new tests in existing fixture; mirror existing test_placement.py style).
- doc delta: BRIEF §20 H-032 (~25 LOC) + lessons L-019 (~15 LOC) + status.md late-night XII (~50 LOC) + TASKS.md fix(T-216c) DONE entry (~30 LOC) + plan doc (this file).

Total feat commit: ~80 LOC; far under §0.3 400 cap. Mirror T-218b ~98 LOC + T-218c ~151 LOC scale (both in same surgical-fix family).

## Acceptance criteria (AC)

1. `placement.py:205-232` retry block wraps `await adapter.get_fill_price(...)` with `try/except (AuthError, NetworkTimeout, RateLimitError) as exc:`.
2. Exception path: warn-log key `execution.get_fill_price_transient_error` with kwargs `bot_id, exchange_order_id, attempt (1-indexed), error`; `fill_price = None`; loop continues per existing None-retry semantics.
3. After-loop block (`if fill_price is None: DLQ + FillPriceUnresolvedError`) unchanged.
4. NEW test `test_handler_retries_when_get_fill_price_raises_NetworkTimeout` proves exception → retry → success on next attempt path; asserts await_count == 2 + warn count == 1.
5. NEW test `test_handler_retries_when_get_fill_price_raises_RateLimitError` proves 2 transient errors → success on 3rd attempt; await_count == 3 + warn count == 2.
6. NEW test `test_handler_retries_when_get_fill_price_raises_AuthError` proves AuthError trio coverage.
7. NEW test `test_fill_price_unresolved_after_all_exception_attempts_publishes_to_dlq_and_raises` proves all-attempt exhaustion with NetworkTimeout side_effect → DLQ + `FillPriceUnresolvedError` raised; awaits == attempts; warn count == attempts; error log `execution.fill_price_unresolved` present.
8. Repo-wide pytest passes (baseline 2091 → 2095 expected; +4 net new tests).
9. Existing 4 fill-price retry tests UNCHANGED (None-return path coverage preserved).
10. NEW H-032 entry in BRIEF §20 (after H-031, before §21 Glossary).
11. NEW L-019 lesson appended to docs/review-lessons.md.
12. TASKS.md fix(T-216c) DONE entry at top of Done section; F5 phase counter UNCHANGED at 26/47.
13. docs/status.md late-night XII section prepended above late-night XI; 7-bug audit progress tracker updates Item 5 → DONE.
14. Commit shape per WG#5: feat (src + tests) staged first; chore (TASKS + BRIEF + lessons + status + plan) staged second; FF-merge to master.

## Write-time guidance

(Plan-reviewer APPROVE 2026-05-09 verbatim 15-item active control list; binding for drift-checker + brief-reviewer Gate 2/3.)

1. Catch tuple verbatim `(AuthError, NetworkTimeout, RateLimitError)` — grep against placement.py:197 (sibling step 5 trio) before commit; broader `Exception` or missing types = REVISE.
2. Exception variable binding `as exc` (match lines 161/176/184/197 convention; not `as e`).
3. New warn log key `execution.get_fill_price_transient_error` — verify uniqueness via repo-wide grep before commit; kwargs `bot_id` + `exchange_order_id` + `attempt` (1-indexed = `attempt + 1`) + `error=str(exc)`.
4. Defensive `fill_price = None` reset inside except block (paranoid against future iteration-N raises after iteration-N-1 success-then-overwrite edge).
5. Existing `if attempt + 1 < fill_price_retry_attempts: await asyncio.sleep(...)` block preserved verbatim — sleep applies to BOTH None-return AND exception-treated-as-None paths uniformly.
6. §N4 TDD ordering: 4 NEW tests written FIRST + verified FAIL with current placement.py code BEFORE applying src patch; verbatim test names per AC #4-7 (drift-checker grep target).
7. NEW tests inserted after line 403 (`test_handler_dlq_publish_failure_still_raises_FillPriceUnresolvedError`) within existing "Fill-price retry" section; reuse `_ok_adapter()` / `_ok_bus()` / `_build()` helpers — no new fixture.
8. Exhaustion test asserts: `await_count == attempts` + `bus.publish.assert_awaited_once()` + `call.args[0] == "orders.dlq.alpha"` + warn count == attempts + `execution.fill_price_unresolved` error key present once + `FillPriceUnresolvedError` raised (mirror lines 384-389 + 401-403).
9. Existing 4 None-path tests UNCHANGED — additive only (per AC #9).
10. H-032 hazard text inserted in BRIEF §20 between line 2834 (end of H-031) and line 2836 (`---` separator before §21 Glossary on line 2838) — verbatim from plan §"NEW H-032 hazard text".
11. L-019 lesson appended to docs/review-lessons.md after L-018 (current EOF on line 84) — verbatim from plan §"NEW L-019 lesson text"; lesson body cites the exact pattern + active control for future retry-loop audits.
12. Commit shape: feat (placement.py + test_placement.py) staged first; chore (TASKS.md + BRIEF + lessons + status + plan-doc) staged second; FF-merge to master per CLAUDE.md task-close pattern. F5 phase counter UNCHANGED at 26/47 (fix() commits don't count; mirror T-218b/T-218c/T-511b2a precedent).
13. Commit message format per memory `feedback_commit_message_format.md`: NO trailer; repeated `-m` flags one per paragraph; never heredoc.
14. Math-validator Gate 4 expected `VERIFIED — out of scope, math-validator skipped` (composition-only fix; no Decimal/float arithmetic; no indicator/seed convention; no P&L computation) — mirror T-218c Gate 4 verdict.
15. Plan-doc itself (`docs/plans/T-216c-fix-fill-price-retry-exception.md`) staged in chore commit per CLAUDE.md gate-1 requirement so drift-checker/brief-reviewer can read WG list during gates 2/3.

## Test plan ordering (§N4 TDD)

1. Read existing test_placement.py "Fill-price retry" section (already done in plan stage).
2. Write 4 NEW tests FIRST (before src change) — all 4 should FAIL with current placement.py code (NetworkTimeout propagates instead of being caught).
3. Verify all 4 fail by running `uv run pytest services/execution/tests/test_placement.py::test_handler_retries_when_get_fill_price_raises_NetworkTimeout -v` etc.
4. Apply src change to placement.py (try/except wrapper + warn log + None-reset).
5. Re-run targeted tests — should all PASS.
6. Re-run repo-wide pytest — baseline 2091 + 4 new = 2095 expected; 0 regressions.
7. Drift-checker Gate 2 → brief-reviewer Gate 3 → math-validator Gate 4 (out-of-scope).

## Open questions

None — all 4 OQs baked at plan time per operator session 2026-05-09.

## Cross-references

- BRIEF §11.3 — adapter error taxonomy (AuthError / NetworkTimeout / RateLimitError / OrderRejected / UnknownState).
- BRIEF §9.5 step 6 — get_fill_price retry contract (T-216a / CONCERN #7 / L-001).
- BRIEF §20 H-030 — open-fill must not decrement remaining_qty (T-218b shipped 2026-05-08).
- BRIEF §20 H-031 — paper adapter must not feed live ExecutionDispatcher (T-218c shipped 2026-05-08).
- TASKS.md `## Done` T-216a / T-216b1 / T-216b2 — original placement pipeline tasks (no retrospective correction needed; T-216c is additive).
- docs/status.md late-night XI — 7-bug operator audit progress tracker (Item 5 = T-216c subject).
- L-017 (T-218b lesson) — test fixtures using artificial post-update mock values mask pre-update bugs.
- L-018 (T-218c lesson) — "dormant in mode" plan claims need code-citation evidence.
- L-019 (this fix) — retry loops over external calls need exception handling at the await site.

## Mirror precedents

- `fix(T-218b-open-fill-qty-bug)` 2026-05-08 — H-030; ~98 LOC; single-pass APPROVE; 4 gates clean. Plan: `docs/plans/T-218b-fix-open-fill-qty.md`.
- `fix(T-218c-paper-dispatcher-skip)` 2026-05-08 — H-031; ~151 LOC; single-pass APPROVE; 4 gates clean. Plan: `docs/plans/T-218c-fix-paper-dispatcher-skip.md`.

T-216c expected to be the smallest of the cluster (~80 LOC); single-pass APPROVE expected; all 4 gates clean.
