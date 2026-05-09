# T-538 — VWAP fill price (closes audit Item 4; H-035)

**Type**: F5 numbered task (NOT a fix; counts toward F5 phase counter).
**Phase**: F5 (unlocked).
**Origin**: derived from operator audit Item 4 (fill-price uses last-trade close, not VWAP) — 7-bug audit 2026-05-08; deferred to T-538 per audit progress tracker. With T-537b shipping 2026-05-09, only Item 4 + Item 6 (TBD) remain in the audit; this task closes Item 4.
**Date**: 2026-05-09.

## Background

`packages/exchange/bybit_v5/adapter.py:273-296` `get_fill_price` returns `Decimal(items[0]["execPrice"])` from a single GET `/v5/execution/list` response, taking ONLY the first execution row. For partial-fill orders (e.g. a 3-leg open at multiple price levels because order qty exceeded one orderbook level's depth), this returns the price of the FIRST leg only — semantically wrong. The correct fill price is the volume-weighted average price (VWAP) across all exec rows:

```
VWAP = Σ(price_i × qty_i) / Σ(qty_i)
```

`placement.py:208` consumes this fill_price and threads it into:
- `compute_sl_price(side, fill_price, sl_pct)` → SL pinned to wrong reference price
- `compute_tp_price(side, fill_price, tp_pct)` → TP pinned to wrong reference price
- `compute_notional_usd(qty, fill_price)` → P&L attribution drift on entry-side cost

For a 3-leg fill at prices [100, 101, 99] with qty [2, 5, 3] (total 10), correct VWAP = 1002/10 = 100.2. Current code returns 100 (first leg). Errors compound: SL/TP off by ~0.2%, notional_usd off by 2 (out of 1002), P&L drift on close per the entry/close differential.

`packages/exchange/paper/adapter.py:1299-1309` `get_fill_price` uses `select_paper_execution_price_by_order_id` which is `LIMIT 1 ORDER BY executed_at ASC` per migration-0008 "Decision #7" comment — same bug shape but currently dormant because PaperExchange synthesizes single-fill events per open. Operator chose VWAP parity (OQ-2 = recommended) so paper tracks live semantics for future partial-fill simulation.

## Operator decisions (2026-05-09 OQ session)

- **OQ-1 = Single-page VWAP with explicit `limit=100` + nextPageCursor warn log**: HTTP request explicitly sets `limit=100` (Bybit `/v5/execution/list` documented max). VWAP computed across ALL items in the response. If response includes `nextPageCursor` (truncated), emit `warning` log key `bybit_v5.get_fill_price_paginated_truncation` so operator sees pathological case (>100 partial fills for one order — extremely rare).
- **OQ-2 = VWAP parity for paper**: NEW SQL helper `select_paper_execution_vwap_by_order_id` computes VWAP via `SUM(price*qty)/SUM(qty)` server-side; replaces `select_paper_execution_price_by_order_id` (LIMIT 1) in the only caller `PaperExchange.get_fill_price`. Old helper kept for any downstream consumer (none today; verified by grep) but deprecated via docstring.
- **OQ-3 = NEW H-035 hazard entry** in BRIEF §20 (after H-034). Pinning the contract.
- **OQ-4 = Full hand-verification by math-validator**: hand-compute fixture for 3-leg fill (prices=[100, 101, 99], qty=[2, 5, 3]) → numerator=200+505+297=1002, denominator=10, VWAP=100.2 exact. Decimal arithmetic throughout (no float casts).

## Scope

### Live: `packages/exchange/bybit_v5/adapter.py`

```python
async def get_fill_price(
    self,
    symbol: str,
    order_id: str,
) -> Decimal | None:
    """Return VWAP across all execution items for ``order_id`` per H-035.

    HTTP: GET /v5/execution/list with explicit limit=100 (Bybit doc max).
    If response carries nextPageCursor (truncated), emit warning log so
    operator sees the rare >100-partial-fill case. Single-page semantic
    per OQ-1; multi-page pagination deferred until concrete operational
    pressure.

    Returns None if no executions for the order_id (caller's retry path
    via T-216c handles).
    """
    await self._limiter.acquire(self._sub_account, "orders")
    try:
        result = await self._client.request(
            "GET",
            "/v5/execution/list",
            params={
                "category": _CATEGORY,
                "symbol": symbol,
                "orderId": order_id,
                "limit": 100,
            },
            retries=3,
        )
    except RateLimitError:
        await self._on_rate_limit_hit("orders")
        raise

    items = result.get("list", [])
    if not items:
        return None

    if result.get("nextPageCursor"):
        logger.warning(
            "bybit_v5.get_fill_price_paginated_truncation",
            symbol=symbol,
            order_id=order_id,
            page_size=len(items),
        )

    # H-035 / T-538: VWAP across all items; NOT items[0].
    numerator = Decimal("0")
    denominator = Decimal("0")
    for item in items:
        price = Decimal(item["execPrice"])
        qty = Decimal(item["execQty"])
        numerator += price * qty
        denominator += qty
    if denominator == 0:
        # Defensive: every Bybit exec row has qty > 0; this should never
        # fire. Return None to surface the anomaly via caller's retry.
        logger.warning(
            "bybit_v5.get_fill_price_zero_total_qty",
            symbol=symbol,
            order_id=order_id,
            item_count=len(items),
        )
        return None
    return numerator / denominator
```

### Paper: `packages/exchange/paper/persistence.py` + `packages/exchange/paper/adapter.py`

Production code reference to OLD helper: only `packages/exchange/paper/adapter.py:1306` (PaperExchange.get_fill_price). Test mocks for OLD helper exist in `test_paper_emission.py` (2 sites) + `test_paper_persistence.py` (2 sites). T-538 repoints `paper/adapter.py` + `test_paper_emission.py` mocks; keeps `test_paper_persistence.py` tests (backward-compat coverage of deprecated helper).

NEW helper `select_paper_execution_vwap_by_order_id`:

```python
async def select_paper_execution_vwap_by_order_id(
    conn: _DbExecutor,
    *,
    exchange_order_id: str,
) -> Decimal | None:
    """SELECT VWAP across paper_executions.price * qty for a given order_id.

    H-035 / T-538: paper parity with live get_fill_price VWAP semantic.
    Returns None if no executions exist.

    SQL: SUM(price * qty) / SUM(qty); NULL when no rows match.
    """
    row = await conn.fetchrow(
        """
        SELECT (SUM(pe.price * pe.qty) / NULLIF(SUM(pe.qty), 0)) AS vwap
        FROM paper_executions pe
        JOIN paper_orders po ON po.id = pe.order_id
        WHERE po.exchange_order_id = $1
        """,
        exchange_order_id,
    )
    if row is None or row["vwap"] is None:
        return None
    vwap: Decimal = row["vwap"]
    return vwap
```

`PaperExchange.get_fill_price` calls the new helper instead of the old `select_paper_execution_price_by_order_id`. Old helper kept for backward-compat (deprecated docstring) since no downstream caller consumes it (verified by grep: only `paper/adapter.py:1306` uses it).

### NEW H-035 hazard text

```
### H-035 — fill_price MUST be VWAP across all executions for an order

**Context.** Both Bybit `/v5/execution/list` and paper_executions can return
multiple exec rows for a single order_id when the order fills at multiple
price levels (partial fills due to orderbook depth, sweeping multiple price
ticks, or split execution across maker/taker). The fill_price threaded into
SL/TP/notional/P&L computations MUST be VWAP across ALL exec rows
(Σ(price×qty)/Σ(qty)), NOT the first row's price. Pre-T-538 code returned
items[0] price only — for a 3-leg fill at [100, 101, 99] qty [2, 5, 3],
returned 100 instead of correct 100.2. Errors compound through SL/TP pin,
notional_usd, P&L attribution. Operator-discovered audit Item 4 (2026-05-08);
fixed via T-538 (2026-05-09).

**Policy.** `ExchangeClient.get_fill_price` MUST compute VWAP using Decimal
arithmetic (NEVER float). For Bybit live: single GET `/v5/execution/list`
with explicit `limit=100` (Bybit doc max for this endpoint), VWAP across
returned items, warn-log if `nextPageCursor` present (truncation indicator).
For paper: SQL aggregate `SUM(price*qty) / NULLIF(SUM(qty), 0)` across
paper_executions rows joined to paper_orders by exchange_order_id. Empty
result → None (caller's T-216c retry path handles).

**Test.** Hand-verifiable fixture: prices=[Decimal("100"), Decimal("101"),
Decimal("99")] × qty=[Decimal("2"), Decimal("5"), Decimal("3")] →
numerator=Decimal("1002"), denominator=Decimal("10"), VWAP=Decimal("100.2")
exact. Both bybit_v5 and paper adapters tested against this fixture.

H-035 numbering note: companion to H-030..H-034 audit cluster (operator
audit 2026-05-08/05-09; H-030 = open-fill remaining_qty; H-031 = paper
adapter must not feed live ExecutionDispatcher; H-032 = retry loop
exception coverage; H-033 = composite-PK position_state UPDATE trade_id
guard; H-034 = outbox relay shutdown ordering). H-035 closes audit Item 4
(fill-price uses last-trade close); H-030..H-035 = audit cluster fully
shipped (Item 6 detail still pending operator).
```

### Tests

**Unit (`packages/exchange/bybit_v5/tests/test_adapter.py`)**:
- `test_get_fill_price_returns_vwap_for_multi_leg_fill` — mock httpx response with 3 items at hand-verifiable prices/qty; assert returns Decimal("100.2") exact.
- `test_get_fill_price_returns_decimal_first_for_single_leg_fill` — mock 1 item; assert returns that item's execPrice unchanged (degenerate VWAP).
- `test_get_fill_price_emits_warning_when_next_page_cursor_present` — mock response with `nextPageCursor="abc"`; assert warning log key `bybit_v5.get_fill_price_paginated_truncation` emitted with item_count.
- `test_get_fill_price_zero_total_qty_returns_none_with_warning` — mock items with qty=0 (defensive; shouldn't happen); assert None + warning.
- `test_get_fill_price_request_uses_explicit_limit_100` — assert HTTP params include `"limit": 100`.
- existing `test_get_fill_price_returns_none_when_empty_list` — UNCHANGED (preserves None contract).

**Unit (`packages/exchange/paper/tests/test_persistence.py`)**:
- `test_select_paper_execution_vwap_by_order_id_returns_vwap_across_rows` — mock-based SQL string + bind ordering test.
- `test_select_paper_execution_vwap_by_order_id_returns_none_when_no_rows` — mock empty.
- `test_select_paper_execution_vwap_by_order_id_returns_none_when_zero_total_qty` — mock NULLIF triggers.

**Unit (`packages/exchange/paper/tests/test_paper_emission.py`)**:
- existing `test_get_fill_price_returns_decimal_when_match` (line ~408) + `test_get_fill_price_returns_none_when_no_match` (line ~421) REPOINTED to NEW helper monkeypatch (`select_paper_execution_vwap_by_order_id` instead of `select_paper_execution_price_by_order_id`). Plan-doc previously listed `test_adapter.py` paper as repoint target — that was stale; `paper/tests/test_adapter.py` does NOT contain `test_get_fill_price_*` (verified grep: all paper get_fill_price tests are in `test_paper_emission.py`).

**Integration (testcontainer-gated; `tests/integration/queries/test_paper_execution_vwap.py` NEW)**:
- Real PG round-trip: insert 3 paper_executions rows (prices=[100, 101, 99], qty=[2, 5, 3]) for one paper_order; call `select_paper_execution_vwap_by_order_id`; assert returns `Decimal("100.2")` exact.
- Single-row degenerate case.
- Empty case → None.

Per L-021 active control: locally execute `POSTGRES_TEST_DSN=... pytest tests/integration/queries/test_paper_execution_vwap.py` BEFORE push.

## Out of scope

- Multi-page pagination (deferred until concrete operational pressure; OQ-1 single-page).
- Old `select_paper_execution_price_by_order_id` helper REMOVAL (deprecated only; backward-compat retained).
- Existing `test_select_paper_execution_price_by_order_id_*` tests in `test_paper_persistence.py:872-900` REMAIN unchanged (per pass-1 CONCERN fix) — backward-compat coverage of the deprecated helper. drift-checker may surface this as "deprecated helper has live tests" — that is INTENDED until a future cleanup task removes the helper entirely.
- Updates to existing `compute_sl_price` / `compute_tp_price` / `compute_notional_usd` (these are correct; only the input fill_price was wrong). T-538 doesn't touch downstream pricing math.

## Files touched

### Source (3 files)

1. `packages/exchange/bybit_v5/adapter.py` — `get_fill_price` rewritten with VWAP loop + explicit limit=100 + nextPageCursor warn + zero-qty defensive None.
2. `packages/exchange/paper/persistence.py` — NEW helper `select_paper_execution_vwap_by_order_id` (SUM/NULLIF SQL); old `select_paper_execution_price_by_order_id` deprecated docstring (kept for backward-compat).
3. `packages/exchange/paper/adapter.py` — `PaperExchange.get_fill_price` calls new helper.

### Tests (5 files)

4. `packages/exchange/bybit_v5/tests/test_adapter.py` — 5 NEW unit tests + 1 existing UNCHANGED (empty-list case).
5. `packages/exchange/paper/tests/test_paper_persistence.py` — 3 NEW unit tests for new helper. Existing 2 tests for old `select_paper_execution_price_by_order_id` (lines 872-900) UNCHANGED — backward-compat coverage retained per "Out of scope" note.
6. `packages/exchange/paper/tests/test_paper_emission.py` (per pass-1 BLOCKER fix) — REPOINT 2 monkeypatch mocks `select_paper_execution_price_by_order_id=AsyncMock(...)` (lines ~415, ~428 in `test_get_fill_price_returns_decimal_when_match` + `test_get_fill_price_returns_none_when_no_match`) to `select_paper_execution_vwap_by_order_id=AsyncMock(...)`. Mock fixtures via `_patch_persistence` (~line 346) operate by `monkeypatch.setattr(persistence, name, value)`, so after `PaperExchange.get_fill_price` switches to the new helper, the OLD mock no longer intercepts → test would hit a real DB call → fail. Repoint MUST land in same feat commit.
7. NEW `tests/integration/queries/test_paper_execution_vwap.py` — testcontainer-gated round-trip.

### Documentation (4 files; chore commit)

8. `docs/CLAUDE_CODE_BRIEF.md` — NEW H-035 entry between H-034 and §21 Glossary.
9. `TASKS.md` — T-538 DONE entry; F5 phase counter advances `29/50 → 30/51`.
10. `docs/status.md` — late-night XVIII section.
11. `docs/plans/T-538-vwap-fill-price.md` — this plan doc.

## LOC budget

- `bybit_v5/adapter.py`: ~30 LOC src delta (VWAP loop + warn log + zero-qty defensive + limit param + module logger import if not already).
- `paper/persistence.py`: ~25 LOC (NEW helper + deprecated docstring).
- `paper/adapter.py`: ~3 LOC (call swap).
- Tests: ~200 LOC (5 + 3 + repoints + 1 testcontainer).
- Total feat commit: ~260 LOC; src ~58 LOC. Far under §0.3 400 src cap.

## Hand verification (per OQ-4 + WG#5)

3-leg fill fixture (verbatim across both adapters' tests + this plan):

| Leg | execPrice | execQty |
|-----|-----------|---------|
| 1   | 100       | 2       |
| 2   | 101       | 5       |
| 3   | 99        | 3       |

- Numerator = 100×2 + 101×5 + 99×3 = 200 + 505 + 297 = **1002**
- Denominator = 2 + 5 + 3 = **10**
- VWAP = 1002 / 10 = **100.2** (exact in Decimal)

Single-leg degenerate case:
- prices=[Decimal("100.5")], qty=[Decimal("3")]
- numerator = 100.5 × 3 = 301.5
- denominator = 3
- VWAP = 301.5 / 3 = **100.5** (returns first leg unchanged per OQ-1 semantic)

Zero-total-qty defensive case:
- prices=[anything], qty=[Decimal("0"), Decimal("0")]
- denominator = 0 → return None + emit `bybit_v5.get_fill_price_zero_total_qty` warning.

All Decimal-arithmetic; no `float()` casts; `str(Decimal(...))` round-trip preserves precision per §5.3.

## Acceptance criteria (AC)

1. `packages/exchange/bybit_v5/adapter.py` `get_fill_price` HTTP request explicitly sets `"limit": 100` per OQ-1.
2. VWAP computed across ALL items via Decimal arithmetic; no `float()` casts.
3. If response carries `nextPageCursor`, emit warning log key `bybit_v5.get_fill_price_paginated_truncation` with `symbol` + `order_id` + `page_size` (`len(items)`) kwargs.
4. Zero-total-qty edge case → return None + emit `bybit_v5.get_fill_price_zero_total_qty` warning. Defensive against empty/malformed rows.
5. Empty `list` → return None (UNCHANGED contract).
6. NEW `packages/exchange/paper/persistence.py select_paper_execution_vwap_by_order_id` uses `SUM(price*qty) / NULLIF(SUM(qty), 0)` SQL aggregate; returns None when result row is None OR `vwap IS NULL`.
7. Old `select_paper_execution_price_by_order_id` kept with deprecated docstring; no caller migration leaves it dangling.
8. `PaperExchange.get_fill_price` updated to call new VWAP helper.
9. NEW H-035 entry in BRIEF §20 (after H-034, before §21 Glossary). Cross-link to T-538 + L-021. H-030..H-035 numbering note included.
10. Tests: 5 NEW bybit_v5 + 3 NEW paper persistence + repointed paper adapter tests + 1 NEW testcontainer integration. Hand-verification fixture verbatim across all relevant tests (3-leg [100,101,99]/[2,5,3] → 100.2).
11. Repo regression: `POSTGRES_TEST_DSN=... uv run pytest -q` → 2249 + ~9 NEW = ~2258 expected. 0 regressions.
12. F5 phase counter advances `29/50 → 30/51` per L-007 / WG convention.
13. Branch `feat/T-538-vwap-fill-price` per CLAUDE.md branching policy. Per L-021: locally execute testcontainer test BEFORE push.
14. Math-validator Gate 4 = full hand-verification per OQ-4. Decimal precision preserved through both adapter implementations; identical fixture across bybit_v5 + paper tests.

14a. **Math-validator scope tension note (per pass-1 CONCERN #3 fix)**: per CLAUDE.md, math-validator Gate 4 default scope is `packages/features/builtins/`, `packages/features/protocols.py`, `packages/features/types.py`, `packages/pnl/`, `services/feature-engine/`, `services/execution/`, `services/scoring/`. T-538 touches `packages/exchange/{bybit_v5,paper}/` which are NOT in default scope. OQ-4 explicitly requests Full hand-verification anyway. Expected math-validator output: either `VERIFIED — out of scope, hand-fixture confirmed` (acknowledging scope-out + verifying the hand fixture per operator request) or `VERIFIED` (if reviewer accepts scope-creep for VWAP arithmetic). Operator info-only — both signals are SHIP-acceptable per OQ-4 intent.

## Test plan ordering (§N4 TDD)

1. Read existing `test_adapter.py` (bybit_v5) for shape reference.
2. Write 5 NEW bybit_v5 tests FIRST. Run; expect FAILS (current code returns items[0] only).
3. Apply `get_fill_price` rewrite in bybit_v5/adapter.py. Re-run; expect PASS.
4. Write 3 NEW paper persistence tests + integration test. Apply new helper SQL. Verify PASS.
5. Update PaperExchange.get_fill_price. Re-run paper adapter tests; expect PASS.
6. Run full repo `POSTGRES_TEST_DSN=... pytest -q --no-cov` — 2249 → ~2258. 0 regressions.
7. Drift-checker Gate 2 → brief-reviewer Gate 3 → math-validator Gate 4 (hand-verification REQUIRED per OQ-4).

## Open questions

None — all 4 OQs baked at plan time per operator session 2026-05-09.

## Cross-references

- BRIEF §11.3 — adapter error taxonomy.
- BRIEF §20 — hazard catalog; H-035 NEW.
- BRIEF §N1 UTC, §N3 idempotency (`@idempotent` on `get_fill_price` preserved — VWAP returns same result for same exec history), §5.3 Decimal precision.
- packages/exchange/bybit_v5/adapter.py:273-296 — current single-leg implementation.
- packages/exchange/paper/persistence.py — new SUM/NULLIF helper alongside existing LIMIT 1 helper.
- TASKS.md `## Done` audit cluster (Items 1+2+3+5+7 done; Item 4 = T-538; Item 6 TBD).
- docs/status.md late-night XVII — last session; T-537 cluster shipped + ci-full green.
- L-021 active control: locally execute testcontainer tests with POSTGRES_TEST_DSN BEFORE push.

## Mirror precedents

- `fix(T-216c-fill-price-retry-exception)` 2026-05-09 — modified the retry loop AROUND `get_fill_price`; T-538 modifies the helper itself. Different scope; complementary.
- T-537a1 testcontainer integration test pattern (mock-based unit + testcontainer round-trip).
- packages/db/queries/audit.py `_to_jsonable` precedent (codec-immune SQL pattern; NA here, no JSONB).

## Write-time guidance

(Plan-reviewer pass-2 APPROVE 2026-05-09 verbatim 3-item active control list; binding for drift-checker + brief-reviewer Gate 2/3.)

1. **Files touched count drift** — sekcia "Tests (5 files)" v plán-doc enumeruje len 4 reálne test files (#4 bybit_v5/test_adapter.py + #5 paper/test_paper_persistence.py + #6 paper/test_paper_emission.py + #7 NEW integration test). Sekcia "Unit (`packages/exchange/paper/tests/test_adapter.py`)" bola stale relikt — `paper/tests/test_adapter.py` skutočne NEMÁ žiadne `test_get_fill_price_*` (verified grep — všetky get_fill_price testy paper sú v `test_paper_emission.py:408, 421`). Implementácia: NETREBA upravovať `paper/tests/test_adapter.py`. Brief-reviewer: Tests (5 files) header → fakticky 4 file dotknutia (bez ohľadu na plán-doc text); ak diff pridáva 5. test súbor, je to scope-creep. (FIXED v plan-doc pri ukladaní pred implementáciou.)

2. **Hand-fixture verbatim parity** (per OQ-4 + AC#10) — fixture prices=[Decimal("100"), Decimal("101"), Decimal("99")] × qty=[Decimal("2"), Decimal("5"), Decimal("3")] → VWAP=Decimal("100.2") MUSÍ byť verbatim identická naprieč všetkými testami (bybit_v5 unit + paper persistence unit + paper integration testcontainer). Žiadne float() casts; žiadne `Decimal("100.2000")` (precision drift); žiadne odlišné prices/qty per-test (cross-test consistency je jediný garant že math-validator hand-verification pokrýva oba adaptery rovnako).

3. **Plán-doc Tests-section cleanup** — pri commitovaní plánu odporúčam stripnúť/opraviť sekciu "Unit (`packages/exchange/paper/tests/test_adapter.py`)" pred uložením do `docs/plans/T-538-vwap-fill-price.md` aby drift-checker neflagoval false-positive "plan mentions test file with no edits". (FIXED at plan-time.)

## Branch step

1. `git checkout -b feat/T-538-vwap-fill-price` BEFORE staging any changes.
2. Feat commit on branch (Source files 1-3 + Tests files 4-7).
3. Chore commit on branch (Documentation files 8-11).
4. Per L-021 active control: locally run `POSTGRES_TEST_DSN=postgresql://scalper:devpass@localhost:5432/postgres uv run pytest tests/integration/queries/test_paper_execution_vwap.py -v` BEFORE push.
5. `git checkout master && git merge --ff-only feat/T-538-vwap-fill-price`.
6. `git push origin master`.
7. `git branch -d feat/T-538-vwap-fill-price`.
8. Verify ci-full + ci-fast + e2e green via `gh run list --branch master --limit 5`.
