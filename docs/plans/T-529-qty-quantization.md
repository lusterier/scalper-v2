# T-529 — qty quantization / pre-flight validation (closes audit Item 6; H-036)

**Type**: F5 numbered task (NOT a fix; counts toward F5 phase counter).
**Phase**: F5 (unlocked).
**Origin**: derived from operator audit Item 6 (qty pre-flight validation TBD; surfaced 2026-05-09 as T-529 critical pre-live blocker per ADR-0011 hardening cluster). Audit cluster Items 1+2+3+4+5+7 shipped 2026-05-08/05-09; T-529 closes Item 6 = LAST audit item.
**Date**: 2026-05-09.

## Background — audit findings

Pre-T-529 qty flow path (from audit-stage analysis 2026-05-09):

1. `bot_config.execution.qty: Decimal` — fixed per-bot qty in `bots.yaml` (operator manually sets).
2. `services/strategy_engine/app/consumer.py:297` — `qty=bot_config.execution.qty` wrapped into `OrderRequest`, published.
3. `services/execution/app/placement.py:154-160` — emits WARN `execution.qty_step_rounding_pending_t_f2_plus` per BLOCKER #3 visibility marker; **performs NO rounding/validation**.
4. `services/execution/app/placement.py:174-178` — passes `request.qty` raw to `adapter.place_market_order(..., qty=request.qty)`.
5. `packages/exchange/bybit_v5/adapter.py:175-186` — forwards `qty=str(qty)` in POST `/v5/order/create` body.

**Existing instruments-info support**: NONE. Verified via grep — no `/v5/market/instruments-info` HTTP call, no qty_step cache, no minOrderQty/minNotional validation.

**Real-world impact (live)**:
- Bybit reject pri `qty mod qtyStep != 0` → "qty precision error" (e.g., BTCUSDT qtyStep=0.001 → qty=0.0015 rejected).
- Bybit reject pri `qty < minOrderQty` (e.g., BTCUSDT minOrderQty=0.001 → qty=0.0005 rejected).
- Bybit reject pri `qty * price < minNotional` (default ~5 USDT).
- Currently bot fires raw qty → high reject rate for non-pre-validated configs; rate-limit token wasted per rejected order.

**Why not surfaced earlier**:
- v2 multi-service NIE JE deployed; sibling v1 testnet stack disabled 2026-05-02.
- T-222 testnet smoke (F2 close-out) was never executed end-to-end.
- Current `bots.yaml` configs have qty values that happen to align with Bybit qtyStep (operator manually verified at config time).
- BLOCKER #3 placement.py warning was a deliberate "operator visibility" placeholder — not a guard.

## Operator decisions (2026-05-09 OQ session)

- **OQ-1 = Single T-529** (NOT split): cache + helper + placement integration tightly coupled. Pre-emptive L-007 split-watch flag pri drift >25%.
- **OQ-2 = Pre-flight reject**: NEW `QtyValidationError(ExchangeError)` raised BEFORE `place_market_order` call. Reject reason includes which constraint failed (`qtyStep` / `minOrderQty`). Avoids wasted Bybit round-trip + rate-limit token + clearer error attribution.
- **OQ-3 = Hardcoded fixture for paper**: `PaperExchange.get_instrument_info` returns hardcoded dict for BTCUSDT/ETHUSDT/SOLUSDT (qtyStep + minOrderQty + minNotional). Mirror live behavior; paper bot exercises same reject scenarios.
- **OQ-4 = NEW H-036 hazard** in BRIEF §20 (after H-035; companion to H-030..H-036 audit cluster).

**Implicit decisions**:
- **Cache TTL**: 1 hour (mirror `set_leverage` adapter-internal cache pattern; instrument metadata changes rarely — delistings, leverage caps — typically multi-day cadence).
- **Cache placement**: adapter-internal LRU dict + TTL check (mirror `_leverage_cache` precedent); NOT shared package-level cache.
- **minNotional pre-flight DEFERRED**: requires last_price (ticker fetch or local OHLC lookup); minNotional pre-flight = T-529-future (small follow-up). T-529 narrow scope = qtyStep + minOrderQty only. Bybit-side rejects minNotional violations as `OrderRejected` per current handler.
- **Tick-size for SL/TP price rounding**: OUT of scope (different concern; T-529c-future or absorbed into T-216b3+ task).

## Scope

### NEW types (`packages/exchange/types.py`)

```python
@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    """Per-symbol instrument metadata for qty pre-flight validation (T-529 / H-036).

    Sourced from Bybit GET /v5/market/instruments-info (live) or hardcoded
    fixture (paper). Cached per-adapter with TTL (default 1h via Settings).

    Fields:
    - qty_step: Decimal — qty must be a multiple of this (round_down semantic).
    - min_order_qty: Decimal — qty must be >= this floor.
    - min_notional_usd: Decimal — qty * price must be >= this (DEFERRED check
      for T-529-future; included on dataclass for forward-compat).
    """
    symbol: str
    qty_step: Decimal
    min_order_qty: Decimal
    min_notional_usd: Decimal
```

### NEW error (`packages/exchange/errors.py`)

```python
class QtyValidationError(ExchangeError):
    """Pre-flight qty validation failed (T-529 / H-036).

    Raised BEFORE place_market_order HTTP call when qty does not satisfy
    instrument constraints (qtyStep alignment OR minOrderQty floor).
    Distinct from OrderRejected which is Bybit-side post-call reject.

    Attributes:
        symbol: instrument symbol.
        constraint: 'qty_step' or 'min_order_qty' (str enum-like).
        actual_qty: qty as submitted (pre-round_down).
        info: InstrumentInfo at validation time.
    """
    def __init__(
        self,
        symbol: str,
        constraint: Literal["qty_step", "min_order_qty"],
        actual_qty: Decimal,
        info: InstrumentInfo,
    ) -> None: ...
```

### NEW protocol method (`packages/exchange/protocols.py`)

```python
class ExchangeClient(Protocol):
    # ... existing methods ...

    @idempotent
    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        """Return cached InstrumentInfo for ``symbol``.

        T-529 / H-036 — pre-flight qty validation source. @idempotent
        because info is metadata; deterministic for same symbol within
        cache TTL window.
        """
        ...
```

### Live adapter (`packages/exchange/bybit_v5/adapter.py`)

```python
@idempotent
async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
    """Fetch Bybit instrument metadata, cached per-adapter with TTL.

    Mirror set_leverage cache pattern: dict-keyed on symbol; check
    `_now_fn() - timestamp <= ttl` BEFORE upstream call. Cache size
    bounded by symbol diversity (typically <20 in operator's bots.yaml).

    HTTP: GET /v5/market/instruments-info?category=linear&symbol=<symbol>.
    Response shape: result.list[0].lotSizeFilter.{qtyStep, minOrderQty,
    minNotionalValue}. Decimal arithmetic preserved per §5.3.
    """
    cached = self._instruments_info_cache.get(symbol)
    if cached is not None and self._now_fn() - cached[1] <= timedelta(seconds=self._instruments_info_cache_ttl_s):
        return cached[0]
    await self._limiter.acquire(self._sub_account, "market")
    try:
        result = await self._client.request(
            "GET",
            "/v5/market/instruments-info",
            params={"category": _CATEGORY, "symbol": symbol},
            retries=3,
        )
    except RateLimitError:
        await self._on_rate_limit_hit("market")
        raise
    items = result.get("list", [])
    if not items:
        msg = f"instrument not found on exchange: {symbol}"
        raise OrderRejected(reason=msg)
    raw = items[0]
    lot = raw["lotSizeFilter"]
    info = InstrumentInfo(
        symbol=symbol,
        qty_step=Decimal(lot["qtyStep"]),
        min_order_qty=Decimal(lot["minOrderQty"]),
        min_notional_usd=Decimal(lot.get("minNotionalValue", "0")),
    )
    self._instruments_info_cache[symbol] = (info, self._now_fn())
    return info
```

Constructor adds `instruments_info_cache_ttl_s: float = 3600.0` kwarg + initializes `self._instruments_info_cache: dict[str, tuple[InstrumentInfo, datetime]] = {}`.

### Paper adapter (`packages/exchange/paper/adapter.py`)

```python
_PAPER_INSTRUMENT_FIXTURES: Final[dict[str, InstrumentInfo]] = {
    "BTCUSDT": InstrumentInfo(
        symbol="BTCUSDT",
        qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"),
        min_notional_usd=Decimal("5"),
    ),
    "ETHUSDT": InstrumentInfo(
        symbol="ETHUSDT",
        qty_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
        min_notional_usd=Decimal("5"),
    ),
    "SOLUSDT": InstrumentInfo(
        symbol="SOLUSDT",
        qty_step=Decimal("0.1"),
        min_order_qty=Decimal("0.1"),
        min_notional_usd=Decimal("5"),
    ),
}

@idempotent
async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
    """T-529 / H-036 — paper parity: hardcoded fixture for known test symbols.

    Maintenance: add new symbol → update _PAPER_INSTRUMENT_FIXTURES.
    Unknown symbol → OrderRejected (mirror live behavior for delisted/typo'd
    symbols).
    """
    info = _PAPER_INSTRUMENT_FIXTURES.get(symbol)
    if info is None:
        msg = f"paper instrument fixture not configured for symbol: {symbol}"
        raise OrderRejected(reason=msg)
    return info
```

### NEW quantization helper (`packages/exchange/quantize.py` — NEW module)

```python
"""Pre-flight qty quantization helper (T-529 / H-036).

Caller-agnostic; consumes InstrumentInfo + raw qty + last_price (optional).
Returns rounded qty if all constraints satisfied; raises QtyValidationError
otherwise. Decimal arithmetic throughout.

Round-down semantic per OQ-1 + §5.3: qty // qty_step * qty_step (NOT round-half-up;
under-shoot is conservative — avoids accidentally exceeding bot.execution.qty
budget).
"""

def quantize_qty(qty: Decimal, info: InstrumentInfo) -> Decimal:
    """Round qty DOWN to qty_step; validate >= min_order_qty.

    minNotional check DEFERRED (T-529-future; requires last_price). Bybit-side
    handles minNotional violations via OrderRejected.

    Raises QtyValidationError on either:
    - qty < info.min_order_qty (qty floor violated)
    - rounded qty < info.min_order_qty (after rounding, below floor)

    Note: qty == 0 after rounding is the only way "rounded qty < min_order_qty"
    with min_order_qty > 0. Treated as min_order_qty violation per UX clarity.
    """
    if qty < info.min_order_qty:
        raise QtyValidationError(info.symbol, "min_order_qty", qty, info)
    rounded = (qty // info.qty_step) * info.qty_step
    if rounded < info.min_order_qty:
        raise QtyValidationError(info.symbol, "min_order_qty", qty, info)
    return rounded
```

### Placement integration (`services/execution/app/placement.py`)

Replace BLOCKER #3 warn-only block (lines 154-160) with pre-flight quantize step:

```python
# T-529 / H-036: pre-flight qty quantization + validation.
# Replaces pre-T-529 BLOCKER #3 warn-only marker.
try:
    instrument_info = await adapter.get_instrument_info(request.symbol)
    quantized_qty = quantize_qty(request.qty, instrument_info)
except QtyValidationError as exc:
    logger.error(
        "execution.qty_validation_failed",
        bot_id=bot_id,
        symbol=request.symbol,
        constraint=exc.constraint,
        actual_qty=str(exc.actual_qty),
        qty_step=str(exc.info.qty_step),
        min_order_qty=str(exc.info.min_order_qty),
    )
    return  # pre-flight reject; no place_market_order call, no NATS publish
except (AuthError, NetworkTimeout, RateLimitError) as exc:
    logger.error(
        "execution.get_instrument_info_failed",
        bot_id=bot_id,
        symbol=request.symbol,
        error=str(exc),
    )
    return
# Use quantized_qty (NOT request.qty) from this point through place_market_order.
```

`request.qty` references in subsequent steps (5 place_market_order, 6+ post-fill) replaced with `quantized_qty`. Note: `request` is `OrderRequest` (immutable Pydantic); we shadow the local `qty` variable.

### Tests

**Unit (`packages/exchange/tests/test_quantize.py` NEW)**:
- `test_quantize_qty_rounds_down_to_qty_step` — qty=Decimal("0.0015"), step=Decimal("0.001") → Decimal("0.001").
- `test_quantize_qty_returns_input_unchanged_when_already_aligned` — qty=Decimal("0.001"), step=Decimal("0.001") → Decimal("0.001").
- `test_quantize_qty_raises_when_qty_below_min_order_qty` — qty=Decimal("0.0005"), min=Decimal("0.001") → QtyValidationError(constraint="min_order_qty").
- `test_quantize_qty_raises_when_rounded_below_min_order_qty` — qty=Decimal("0.0009"), step=Decimal("0.001"), min=Decimal("0.001") → rounded=0 → QtyValidationError.

**Unit (`packages/exchange/bybit_v5/tests/test_adapter.py`)**:
- `test_get_instrument_info_calls_upstream_with_market_endpoint` — verifies HTTP shape.
- `test_get_instrument_info_caches_within_ttl` — second call within TTL → no upstream.
- `test_get_instrument_info_re_calls_upstream_after_ttl_expires` — past TTL → fresh call.
- `test_get_instrument_info_raises_order_rejected_when_instrument_not_found` — empty list response → OrderRejected.
- `test_get_instrument_info_parses_lot_size_filter_into_decimal` — qtyStep/minOrderQty/minNotional Decimal preservation.

**Unit (`packages/exchange/paper/tests/test_paper_emission.py`)**:
- `test_get_instrument_info_returns_fixture_for_btcusdt` — known fixture lookup.
- `test_get_instrument_info_raises_order_rejected_for_unknown_symbol` — fixture miss.

**Unit (`services/execution/tests/test_placement.py`)**:
- `test_placement_quantizes_qty_before_place_market_order` — request.qty=0.0015 → adapter.place_market_order called with quantized 0.001.
- `test_placement_pre_flight_rejects_when_qty_below_min_order_qty` — request.qty=0.0005 → no place_market_order call + log key `execution.qty_validation_failed`.
- `test_placement_pre_flight_rejects_when_get_instrument_info_raises_auth_error` — get_instrument_info side_effect AuthError → log `execution.get_instrument_info_failed` + return.
- existing `test_*_qty_step_rounding_pending_t_f2_plus` REMOVED (warn key no longer emitted).

**Integration (testcontainer-gated; deferred — no DB roundtrip in T-529; quantize is pure Decimal arithmetic + adapter cache)**: N/A.

### NEW H-036 hazard text

```
### H-036 — Pre-flight qty validation: qtyStep / minOrderQty before place_market_order

**Context.** Bybit `/v5/order/create` rejects orders with `qty mod qtyStep != 0`
("qty precision error") OR `qty < minOrderQty` OR `qty * price < minNotional`.
Pre-T-529 placement.py emitted only a warn-key `execution.qty_step_rounding_pending_t_f2_plus`
(BLOCKER #3 visibility marker) and forwarded raw `request.qty` to
`place_market_order`. For non-pre-aligned operator configs, this resulted in
high reject rate per Bybit-side `OrderRejected` + wasted rate-limit token.
v2 multi-service is not yet deployed; T-222 testnet smoke never executed
end-to-end; bug surfaced via operator audit Item 6 (2026-05-08), surfaced as
T-529 critical pre-live blocker 2026-05-09.

**Policy.** Placement handler MUST pre-flight validate qty against instrument
metadata BEFORE `place_market_order`:

1. `await adapter.get_instrument_info(symbol)` → `InstrumentInfo` (cached
   per-adapter with TTL; live = Bybit `/v5/market/instruments-info`; paper =
   hardcoded fixture for BTCUSDT/ETHUSDT/SOLUSDT).
2. `quantized_qty = quantize_qty(request.qty, info)`:
   - Round DOWN to `qty_step` (`qty // qty_step * qty_step`).
   - Raise `QtyValidationError(constraint="min_order_qty")` if either pre-round
     qty < `min_order_qty` OR rounded qty < `min_order_qty`.
3. Use `quantized_qty` (NOT `request.qty`) through rest of pipeline.
4. On `QtyValidationError`: log `execution.qty_validation_failed` with
   constraint + actual_qty + qty_step + min_order_qty kwargs; return early
   (pre-flight reject; no Bybit round-trip; no NATS publish).
5. On `get_instrument_info` upstream errors (AuthError / NetworkTimeout /
   RateLimitError): log `execution.get_instrument_info_failed` + return.

**minNotional pre-flight DEFERRED** to T-529-future (requires last_price
fetch — out of T-529 narrow scope). Bybit-side rejects minNotional via
existing `OrderRejected` handler.

**Test.** `test_quantize_qty_*` (4 unit tests) + `test_get_instrument_info_*`
(5 bybit_v5 unit tests + 2 paper unit tests) + `test_placement_quantizes_qty_*`
+ `test_placement_pre_flight_rejects_*` (3 placement integration tests).

H-036 numbering note: companion to H-030..H-035 audit cluster (operator audit
2026-05-08/05-09). H-036 closes audit Item 6 (qty pre-flight validation).
H-030..H-036 = audit cluster fully shipped at T-529 ship.
```

## Out of scope

- **minNotional pre-flight**: deferred to T-529-future. Requires last_price (ticker fetch or local OHLC lookup). Out of T-529 narrow scope. Bybit-side rejects minNotional violations via existing `OrderRejected` handler.
- **Tick-size for SL/TP price rounding**: separate concern; defer to T-529c-future or absorb into T-216b3+ task.
- **Risk-per-SL sizing (T-528)**: T-529 quantizes whatever qty is provided. T-528 (separate F5 task) will compute qty from balance + SL distance; T-529 then quantizes T-528's output.
- **Removal of old WARN key `execution.qty_step_rounding_pending_t_f2_plus`**: removed at T-529 ship per AC#10 (no longer applicable).
- **Sibling adapter migration** (no other ExchangeClient implementations exist; bybit_v5 + paper are the only two).

## Files touched

### Source (6 files)

1. NEW `packages/exchange/types.py` (`InstrumentInfo` dataclass added).
2. UPDATED `packages/exchange/errors.py` (NEW `QtyValidationError(ExchangeError)`).
3. UPDATED `packages/exchange/protocols.py` (NEW `get_instrument_info` Protocol method).
4. UPDATED `packages/exchange/bybit_v5/adapter.py` (NEW `get_instrument_info` impl + `_instruments_info_cache` + `instruments_info_cache_ttl_s` ctor kwarg).
5. UPDATED `packages/exchange/paper/adapter.py` (NEW `get_instrument_info` hardcoded fixture impl + `_PAPER_INSTRUMENT_FIXTURES` Final dict).
6. NEW `packages/exchange/quantize.py` (`quantize_qty` helper).
7. UPDATED `services/execution/app/placement.py` (replace BLOCKER #3 warn-only with pre-flight quantize block).

### Tests (4 files)

8. NEW `packages/exchange/tests/test_quantize.py` (4 unit tests).
9. UPDATED `packages/exchange/bybit_v5/tests/test_adapter.py` (5 NEW unit tests for `get_instrument_info`).
10. UPDATED `packages/exchange/paper/tests/test_paper_emission.py` (2 NEW unit tests for `get_instrument_info`).
11. UPDATED `services/execution/tests/test_placement.py` (3 NEW unit tests for placement quantize integration; 1 obsolete test REMOVED — old qty_step_rounding_pending_t_f2_plus warn assertion).

### Documentation (4 files; chore commit)

12. `docs/CLAUDE_CODE_BRIEF.md` — NEW H-036 entry between H-035 and §21 Glossary.
13. `TASKS.md` — T-529 DONE entry; F5 phase counter advances `30/51 → 31/52`.
14. `docs/status.md` — late-night XIX section.
15. `docs/plans/T-529-qty-quantization.md` — this plan doc.

## LOC budget

- types.py: +20 LOC (InstrumentInfo dataclass + module additions).
- errors.py: +25 LOC (QtyValidationError class + __init__ + docstring).
- protocols.py: +15 LOC (Protocol method addition + docstring).
- bybit_v5/adapter.py: +60 LOC (get_instrument_info + cache state + ctor kwarg).
- paper/adapter.py: +35 LOC (fixture dict + get_instrument_info impl).
- quantize.py: +50 LOC (NEW module + quantize_qty helper).
- placement.py: net +5 LOC (replace 7-line warn block with ~12-line pre-flight; rest unchanged).
- Tests: ~250 LOC across 4 test files.
- Total feat commit: ~460 LOC; src ~210 LOC. Under §0.3 400 src cap by margin.

L-007 split-watch flag pre-emptively if drift exceeds +25% src (i.e. >262 src LOC). Mirror L-014 active control for new-infra cohort.

## Acceptance criteria (AC)

1. NEW `InstrumentInfo` dataclass in `packages/exchange/types.py` with 4 fields (symbol, qty_step, min_order_qty, min_notional_usd) all `Decimal`-typed (frozen, slots).
2. NEW `QtyValidationError(ExchangeError)` in `packages/exchange/errors.py` with `__init__(symbol, constraint: Literal["qty_step", "min_order_qty"], actual_qty, info)` signature.
3. NEW `get_instrument_info(symbol: str) -> InstrumentInfo` Protocol method in `packages/exchange/protocols.py` with `@idempotent` marker.
4. `BybitV5Adapter.get_instrument_info` impl: HTTP GET `/v5/market/instruments-info` with `params={"category": _CATEGORY, "symbol": symbol}` using **existing module-level `_CATEGORY = "linear"` constant** (per pass-1 CONCERN fix; not hardcoded "linear" string). LRU/TTL cache (default 3600s; ctor kwarg `instruments_info_cache_ttl_s` mirroring `_DEFAULT_LEVERAGE_CACHE_TTL_S` precedent — ctor default ONLY, NOT YAML/Settings-exposed in T-529 narrow scope). Empty list response → `OrderRejected("instrument not found on exchange: <symbol>")`. `@idempotent` decorator preserved.
5. `PaperExchange.get_instrument_info` impl: hardcoded fixture dict for BTCUSDT/ETHUSDT/SOLUSDT; unknown symbol → `OrderRejected("paper instrument fixture not configured for symbol: <symbol>")`. `@idempotent` decorator preserved.
6. NEW `quantize_qty(qty: Decimal, info: InstrumentInfo) -> Decimal` helper in `packages/exchange/quantize.py`: rounds qty DOWN to qty_step (`qty // qty_step * qty_step`); raises `QtyValidationError(constraint="min_order_qty")` when (a) qty < min_order_qty, or (b) rounded qty < min_order_qty.
7. `placement.py` replaces BLOCKER #3 warn-only block with pre-flight quantize: `await adapter.get_instrument_info(symbol)` + `quantize_qty(request.qty, info)`; on `QtyValidationError` → log `execution.qty_validation_failed` + return early (NO place_market_order call); on `(AuthError, NetworkTimeout, RateLimitError)` from get_instrument_info → log `execution.get_instrument_info_failed` + return.
8. Quantized qty (NOT request.qty) used in subsequent placement steps (place_market_order + post-fill pipeline).
9. Tests: 4 NEW quantize_qty unit tests + 5 NEW bybit_v5 get_instrument_info unit tests + 2 NEW paper get_instrument_info unit tests + 3 NEW placement integration tests.
10. Existing `test_*_qty_step_rounding_pending_t_f2_plus` test REMOVED (warn key no longer emitted post-T-529).
11. NEW H-036 entry in BRIEF §20 (after H-035, before §21 Glossary). H-030..H-036 audit cluster note.
12. TASKS.md T-529 DONE entry; F5 counter advances `30/51 → 31/52`.
13. Repo regression: pytest 2254 → ~2268 expected (+~14 net new tests).
14. Branch `feat/T-529-qty-quantization` per CLAUDE.md.
15. Math-validator Gate 4: full hand-verification of quantize_qty arithmetic — `Decimal("0.0015") // Decimal("0.001") * Decimal("0.001") == Decimal("0.001")` exact (Decimal `//` is floor-div per Python spec; preserves precision).

16. **(NEW per pass-1 BLOCKER fix)** Quantized qty substitution at ALL 6 production-path `request.qty` sites in `placement.py` post-pre-flight block, not just `place_market_order`. Audited 2026-05-09 grep-verified sites:
    - L177: `adapter.place_market_order(..., qty=request.qty)` → `quantized_qty`
    - L254: `compute_tp_size(request.qty, request.tp_qty_pct)` → `compute_tp_size(quantized_qty, ...)` — partial-TP qty would otherwise hit Bybit qty_step reject same as primary order
    - L255: `compute_notional_usd(request.qty, fill_price)` → `compute_notional_usd(quantized_qty, ...)` — notional should reflect actual-sent qty
    - L286: paper-mode shadow_start emit `qty=request.qty` → `qty=quantized_qty`
    - L388: `persist_placement_tx(qty=request.qty, ...)` → `qty=quantized_qty` — DB-persisted qty matches actual placement
    - L402: NATS emit `qty=request.qty` → `qty=quantized_qty` — downstream consumer (T-218 dispatcher) sees actual-sent qty
    L159 warn-log block REMOVED entirely (BLOCKER #3 marker no longer applicable). Brief-reviewer Gate 3 grep target: `request.qty` in placement.py post-quantize block must return 0 hits (only pre-quantize block uses raw `request.qty`).
17. **(NEW per pass-1 BLOCKER fix)** NEW placement test `test_placement_uses_quantized_qty_in_all_downstream_calls` — request.qty=0.0015, fixture qty_step=0.001 → asserts:
    - `adapter.place_market_order` call kwargs `qty == Decimal("0.001")`
    - `compute_tp_size` call args[0] == Decimal("0.001")
    - `compute_notional_usd` call args[0] == Decimal("0.001")
    - `persist_placement_tx` call kwargs `qty == Decimal("0.001")`
    - NATS publish payload qty == Decimal("0.001")
    Pin pattern mirror L-019 active control (T-216c retry exception): "guards inside main path can be bypassed by sibling code — verify ALL sites".

## Hand verification

3-case hand-fixture (math-validator Gate 4 target):

| Case | qty (in) | qty_step | min_order_qty | Expected output |
|------|----------|----------|---------------|-----------------|
| Aligned | Decimal("0.001") | Decimal("0.001") | Decimal("0.001") | Decimal("0.001") (unchanged) |
| Round-down | Decimal("0.0015") | Decimal("0.001") | Decimal("0.001") | Decimal("0.001") (rounded down) |
| Below floor pre-round | Decimal("0.0005") | Decimal("0.001") | Decimal("0.001") | QtyValidationError(min_order_qty) |
| Below floor post-round | Decimal("0.0009") | Decimal("0.001") | Decimal("0.001") | QtyValidationError(min_order_qty) — rounds to 0 |

Decimal `//` semantic verification:
- `Decimal("0.0015") // Decimal("0.001")` = Decimal("1") (Python floor-div on Decimal; rounds toward -inf).
- `Decimal("1") * Decimal("0.001")` = Decimal("0.001") exact.
- `Decimal("0.0009") // Decimal("0.001")` = Decimal("0") → `0 * 0.001 = 0` < min_order_qty → raise.

All Decimal arithmetic; no `float()` casts; precision preserved per §5.3.

## Test plan ordering (§N4 TDD)

1. Read existing `bybit_v5/tests/test_adapter.py` set_leverage cache test for shape reference.
2. Write 4 NEW quantize_qty tests FIRST (test_quantize.py); RED.
3. Apply NEW types.py + errors.py + quantize.py; verify test_quantize.py GREEN.
4. Write 5 NEW bybit_v5 get_instrument_info tests + 2 NEW paper tests; RED.
5. Apply protocols.py + bybit_v5 + paper adapter changes; verify GREEN.
6. Write 3 NEW placement integration tests + REMOVE 1 obsolete; RED.
7. Apply placement.py refactor; verify GREEN.
8. Run repo-wide pytest. Expect ~14 net new passes; 0 regressions.
9. Drift-checker Gate 2 → brief-reviewer Gate 3 → math-validator Gate 4 (hand-verification REQUIRED — quantize_qty Decimal arithmetic in scope; placement.py is in services/execution/ which IS in default math-validator scope).

## Open questions

None — all 4 OQs baked at plan time per operator session 2026-05-09 + implicit decisions documented (cache TTL 1h; minNotional pre-flight DEFERRED).

## Cross-references

- BRIEF §11.3 — adapter error taxonomy (NEW QtyValidationError extends ExchangeError).
- BRIEF §20 — hazard catalog; H-036 NEW.
- BRIEF §N1 UTC, §N3 idempotency (`@idempotent` on get_instrument_info), §5.3 Decimal precision.
- ADR-0011 — F5 pre-live operational hardening cluster (T-529 = qty quantization member task).
- packages/exchange/bybit_v5/adapter.py:155-160 — set_leverage cache pattern (T-529 mirrors).
- T-528 (deferred) — risk-per-SL sizing; T-529 quantizes T-528's output post-ship.
- L-021 active control: testcontainer-gated tests local exercise — N/A here (no DB roundtrip in T-529 helpers).

## Mirror precedents

- `T-538 VWAP fill price` 2026-05-09 — last shipped audit task; similar shape (NEW packages/exchange helper + adapter integration + hand-verified math). T-529 mirrors structure.
- `T-216c fill_price retry exception swallowing` 2026-05-09 — placement.py error-taxonomy refactor pattern.

## Branch step

1. `git checkout -b feat/T-529-qty-quantization` BEFORE staging.
2. Feat commit on branch (Source files 1-7 + Tests 8-11).
3. Chore commit on branch (Docs 12-15).
4. `git checkout master && git merge --ff-only feat/T-529-qty-quantization`.
5. `git push origin master`.
6. `git branch -d feat/T-529-qty-quantization`.
7. Verify ci-full + ci-fast + e2e green via `gh run list --branch master --limit 5`.
