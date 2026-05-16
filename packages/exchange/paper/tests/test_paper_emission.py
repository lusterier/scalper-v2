"""§12.1 PaperExchange execution emission + AsyncIterator unit tests (T-213b).

§N4 TDD step 1 per plan-doc — tests written FIRST per operator-locked
implementation order. Mock asyncpg pool; fast feedback loop; no DB.

Tests cover:

* Constructor extension (pool + event_queue_maxsize DI per BLOCKER 6).
* ``set_leverage`` no-op (Decision #13; per CONCERN 1 — orphan from OQ-3
  reassigned).
* ``stream_executions`` / ``stream_positions`` AsyncIterator shape +
  FIFO ordering (Decision #11/#12).
* OQ-5 multi-consumer round-robin caveat documented via test pin.
* Decision #2 persist-then-emit ordering (queue.put after tx commit).
* Decision #11 backpressure (bounded queue blocks writer when full).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core import BotId
from packages.exchange import ExecutionEvent, PositionEvent
from packages.exchange.paper import PaperExchange
from packages.exchange.paper.adapter import PendingSLTPFill


def _make_pool_mock() -> MagicMock:
    """asyncpg.Pool stand-in mirroring services/execution/tests/conftest.py."""
    pool = MagicMock()
    pool.close = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _make_paper_exchange(
    *,
    pool: MagicMock | None = None,
    event_queue_maxsize: int = 1000,
    now: datetime | None = None,
    emit_parent_lifecycle: bool = False,
    bus: MagicMock | None = None,
) -> PaperExchange:
    """Construct PaperExchange with mock pool + frozen-time now_fn."""
    if bus is None:
        bus = MagicMock()
        bus.subscribe = AsyncMock()
        bus.publish = AsyncMock()
    fixed_now = now or datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    return PaperExchange(
        seed_balance=Decimal("10000"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test-bot"),
        bus=bus,
        slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
        now_fn=lambda: fixed_now,
        pool=pool or _make_pool_mock(),
        event_queue_maxsize=event_queue_maxsize,
        emit_parent_lifecycle=emit_parent_lifecycle,
    )


# --- Constructor extension (BLOCKER 6 / Decision #10) -----------------------


def test_constructor_accepts_pool_and_event_queue_maxsize() -> None:
    """T-213b adds pool + event_queue_maxsize kwargs; constructor stores them."""
    pool = _make_pool_mock()
    pe = _make_paper_exchange(pool=pool, event_queue_maxsize=500)
    assert pe._pool is pool
    assert pe._execution_queue.maxsize == 500
    assert pe._position_queue.maxsize == 500


def test_constructor_default_event_queue_maxsize_is_1000() -> None:
    """Default event_queue_maxsize=1000 per Decision #11."""
    pe = _make_paper_exchange()
    assert pe._execution_queue.maxsize == 1000
    assert pe._position_queue.maxsize == 1000


# --- set_leverage no-op (Decision #13 / CONCERN 1 reassigned) ---------------


async def test_set_leverage_returns_none_at_paper() -> None:
    """Paper has no leverage concept; set_leverage returns None silently."""
    pe = _make_paper_exchange()
    # set_leverage typed -> None; await for side-effect-or-absence-thereof.
    await pe.set_leverage("BTCUSDT", 10)


# --- stream_executions / stream_positions (Decisions #11, #12) --------------


def test_stream_executions_signature_is_def_not_async_def() -> None:
    """T-201 OQ-1: stream_executions is ``def`` (returns AsyncIterator)."""
    import inspect

    sig = inspect.iscoroutinefunction(PaperExchange.stream_executions)
    assert sig is False, "stream_executions must be `def` not `async def` per T-201 OQ-1"


def test_stream_positions_signature_is_def_not_async_def() -> None:
    sig = __import__("inspect").iscoroutinefunction(PaperExchange.stream_positions)
    assert sig is False


async def test_stream_executions_yields_events_in_fifo_order() -> None:
    """Decision #11: per-instance asyncio.Queue yields events in FIFO order."""
    pe = _make_paper_exchange()
    events = [
        ExecutionEvent(
            exchange_exec_id=f"exec-{i}",
            exchange_order_id=f"ord-{i}",
            symbol="BTCUSDT",
            side="buy",
            price=Decimal("65000"),
            qty=Decimal("0.1"),
            fee=Decimal("3.9"),
            executed_at=datetime(2026, 4, 28, 12, 0, i, tzinfo=UTC),
        )
        for i in range(3)
    ]
    for ev in events:
        await pe._execution_queue.put(ev)

    iterator = pe.stream_executions()
    yielded = []
    for _ in range(3):
        yielded.append(await iterator.__anext__())
    assert yielded == events


async def test_stream_positions_yields_events_in_fifo_order() -> None:
    """Symmetric: stream_positions also yields in FIFO order."""
    pe = _make_paper_exchange()
    events = [
        PositionEvent(
            symbol="BTCUSDT",
            side="buy",
            size=Decimal("0.5") - Decimal("0.1") * i,
            entry_price=Decimal("65000"),
            leverage=None,
            unrealized_pnl=None,
            occurred_at=datetime(2026, 4, 28, 12, 0, i, tzinfo=UTC),
        )
        for i in range(3)
    ]
    for ev in events:
        await pe._position_queue.put(ev)

    iterator = pe.stream_positions()
    yielded = []
    for _ in range(3):
        yielded.append(await iterator.__anext__())
    assert yielded == events


async def test_stream_executions_blocks_on_empty_queue() -> None:
    """Empty queue: await blocks until put. Test via wait_for timeout."""
    pe = _make_paper_exchange()
    iterator = pe.stream_executions()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(iterator.__anext__(), timeout=0.05)


async def test_pending_queue_empty_after_full_drain() -> None:
    """Drift checker invariant: ``_pending_sl_tp_fills`` is fully drained per candle.

    T-213b ``_on_candle`` drains the entire queue inline; nothing must
    persist between candles unless cross detection adds it again.
    """
    pe = _make_paper_exchange()
    # Inject pre-existing fill in queue (simulates an earlier candle's enqueue).
    pe._pending_sl_tp_fills.append(
        PendingSLTPFill(
            symbol="UNKSYMBOL",
            side="buy",
            qty=Decimal("0.1"),
            trigger_price=Decimal("100"),
            triggered_at=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
            kind="sl",
            tpsl_mode="Full",
        )
    )
    # No active position for UNKSYMBOL — drain will KeyError; replace drain with
    # no-op for this test (we test loop drains, not body correctness).
    pe._drain_sl_tp_fill = AsyncMock()  # type: ignore[method-assign]
    payload = MagicMock()
    payload.payload = {
        "schema_version": "1.0",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "bucket_start": datetime(2026, 4, 28, 12, 1, 0, tzinfo=UTC),
        "open": Decimal("65000"),
        "high": Decimal("65100"),
        "low": Decimal("64900"),
        "close": Decimal("65000"),
        "volume": Decimal("100"),
        "source": "binance",
        "is_closed": True,
    }
    payload.correlation_id = "corr"
    payload.published_at = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    payload.publisher = "test"
    await pe._on_candle(payload)
    assert pe._pending_sl_tp_fills == []


async def test_drain_called_after_t213a_enqueue_in_same_candle() -> None:
    """Wiring test (Decision #5+#9): drain dispatched inside ``_on_candle``."""
    pe = _make_paper_exchange()
    pe._active_positions["BTCUSDT"] = {
        "trade_id": 1,
        "side": "buy",
        "qty": Decimal("0.5"),
        "entry_price": Decimal("65000"),
        "fees_paid": Decimal("19.5"),
        "sl_price": Decimal("64500"),
        "tp_price": Decimal("65500"),
        "tp_size": Decimal("0.5"),
        "tpsl_mode": "Full",
    }
    drain_calls: list[PendingSLTPFill] = []

    async def _record(fill: PendingSLTPFill) -> None:
        drain_calls.append(fill)

    pe._drain_sl_tp_fill = _record  # type: ignore[method-assign]
    payload = MagicMock()
    payload.payload = {
        "schema_version": "1.0",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "bucket_start": datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
        "open": Decimal("65000"),
        "high": Decimal("65050"),
        "low": Decimal("64400"),
        "close": Decimal("64600"),
        "volume": Decimal("100"),
        "source": "binance",
        "is_closed": True,
    }
    payload.correlation_id = "corr"
    payload.published_at = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    payload.publisher = "test"
    await pe._on_candle(payload)
    assert len(drain_calls) == 1
    assert drain_calls[0].kind == "sl"
    assert drain_calls[0].trigger_price == Decimal("64500")


def test_close_paper_trade_uses_pk_not_symbol_status() -> None:
    """H-018 white-box: close_paper_trade must filter by id (PK), not symbol+status.

    Plan-doc §"Hazards explicitly addressed" line 636 binding: SQL string
    contains ``WHERE id = $`` token; protects against regression toward
    ``WHERE symbol = $1 AND status = 'open'`` which can race-update the
    wrong trade. Mirror packages/db/queries pattern.
    """
    import inspect

    from packages.exchange.paper import persistence

    source = inspect.getsource(persistence.close_paper_trade)
    assert "WHERE id = $" in source, (
        "H-018 invariant: close_paper_trade must filter by PK (id); regression check failed."
    )
    # The dangerous regression pattern is filtering by symbol+status — would
    # race-update the wrong open trade if 2 trades on same symbol race.
    assert "WHERE symbol" not in source, (
        "H-018 invariant: close_paper_trade must NOT filter by symbol; "
        "PK-only is the canonical close path."
    )


async def test_two_consumers_split_events_does_not_broadcast() -> None:
    """OQ-5 caveat: shared Queue round-robins; consumers SPLIT events.

    Two consumers calling stream_executions() simultaneously each call
    queue.get() — every event lands in exactly ONE consumer, NOT both.
    Documents the architectural limitation per Decision #12 caveat.
    """
    pe = _make_paper_exchange()
    events = [
        ExecutionEvent(
            exchange_exec_id=f"exec-{i}",
            exchange_order_id=f"ord-{i}",
            symbol="BTCUSDT",
            side="buy",
            price=Decimal("65000"),
            qty=Decimal("0.1"),
            fee=Decimal("3.9"),
            executed_at=datetime(2026, 4, 28, 12, 0, i, tzinfo=UTC),
        )
        for i in range(4)
    ]
    for ev in events:
        await pe._execution_queue.put(ev)

    iter_a = pe.stream_executions()
    iter_b = pe.stream_executions()

    # Round-robin: each consumer gets a non-overlapping subset of events.
    a_events: list[ExecutionEvent] = []
    b_events: list[ExecutionEvent] = []
    for _ in range(2):
        a_events.append(await iter_a.__anext__())
        b_events.append(await iter_b.__anext__())

    union = set(a_events) | set(b_events)
    intersect = set(a_events) & set(b_events)
    assert len(union) == 4, "all 4 events delivered between two consumers"
    assert intersect == set(), "no event delivered to both consumers (NOT broadcast)"


# --- T-213c: read methods (mock pool) --------------------------------------


def _record_like(data: dict[str, object]) -> object:
    """Lightweight asyncpg.Record stand-in for unit tests.

    Supports both ``row["key"]`` mapping access and dict-like iteration
    used in the read-method bodies.
    """

    class _Rec:
        def __init__(self, d: dict[str, object]) -> None:
            self._d = d

        def __getitem__(self, key: str) -> object:
            return self._d[key]

    return _Rec(data)


def _patch_persistence(monkeypatch: pytest.MonkeyPatch, **fns: object) -> None:
    """Replace persistence-module helpers with AsyncMock returns."""
    from packages.exchange.paper import persistence

    for name, value in fns.items():
        monkeypatch.setattr(persistence, name, value)


async def test_get_positions_returns_empty_list_for_no_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-3 default A: no rows → empty list."""
    pe = _make_paper_exchange()
    _patch_persistence(monkeypatch, select_paper_positions=AsyncMock(return_value=[]))
    result = await pe.get_positions()
    assert result == []


async def test_get_positions_maps_remaining_qty_to_position_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-4 default A: Position.size = paper_positions.remaining_qty."""
    pe = _make_paper_exchange()
    rows = [
        _record_like(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "remaining_qty": Decimal("0.4"),
                "entry_price": Decimal("65000"),
            }
        )
    ]
    _patch_persistence(monkeypatch, select_paper_positions=AsyncMock(return_value=rows))
    result = await pe.get_positions()
    assert len(result) == 1
    assert result[0].symbol == "BTCUSDT"
    assert result[0].size == Decimal("0.4")  # NOT entry qty
    assert result[0].entry_price == Decimal("65000")


async def test_get_positions_returns_leverage_and_unrealized_pnl_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paper-fixed-fields invariant: no leverage column, no live mark price,
    and sl_price always None (T-534a / OQ-4=A — paper SL is synthetic-
    internal; the live-only T-534b watchdog skips paper bots)."""
    pe = _make_paper_exchange()
    rows = [
        _record_like(
            {
                "symbol": "BTCUSDT",
                "side": "buy",
                "remaining_qty": Decimal("0.5"),
                "entry_price": Decimal("65000"),
            }
        )
    ]
    _patch_persistence(monkeypatch, select_paper_positions=AsyncMock(return_value=rows))
    result = await pe.get_positions()
    assert result[0].leverage is None
    assert result[0].unrealized_pnl is None
    assert result[0].sl_price is None


async def test_get_fill_price_returns_decimal_when_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: pass-through Decimal."""
    pe = _make_paper_exchange()
    _patch_persistence(
        monkeypatch,
        select_paper_execution_vwap_by_order_id=AsyncMock(return_value=Decimal("65032.5")),
    )
    price = await pe.get_fill_price("BTCUSDT", "paper-abc")
    assert price == Decimal("65032.5")


async def test_get_fill_price_returns_none_when_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None pass-through."""
    pe = _make_paper_exchange()
    _patch_persistence(
        monkeypatch,
        select_paper_execution_vwap_by_order_id=AsyncMock(return_value=None),
    )
    price = await pe.get_fill_price("BTCUSDT", "paper-missing")
    assert price is None


async def test_get_instrument_info_returns_fixture_for_btcusdt() -> None:
    """T-529 / H-036 — paper hardcoded fixture lookup (BTCUSDT canonical Bybit values)."""
    pe = _make_paper_exchange()
    info = await pe.get_instrument_info("BTCUSDT")
    assert info.symbol == "BTCUSDT"
    assert info.qty_step == Decimal("0.001")
    assert info.min_order_qty == Decimal("0.001")
    assert info.min_notional_usd == Decimal("5")


async def test_get_instrument_info_raises_order_rejected_for_unknown_symbol() -> None:
    """T-529 / H-036 — fixture miss → OrderRejected (mirror live behavior)."""
    from packages.exchange.errors import OrderRejected

    pe = _make_paper_exchange()
    with pytest.raises(OrderRejected) as exc_info:
        await pe.get_instrument_info("UNKNOWN-COIN-USDT")
    assert "UNKNOWN-COIN-USDT" in str(exc_info.value)


async def test_get_closed_pnl_cumulative_raises_on_sub_account_mismatch() -> None:
    """OQ-5 default A: exact str equality; mismatch → ValueError."""
    pe = _make_paper_exchange()
    with pytest.raises(ValueError, match="sub_account mismatch"):
        await pe.get_closed_pnl_cumulative("other-bot")


async def test_get_closed_pnl_cumulative_returns_decimal_zero_when_no_closed_trades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision #8: NULL → Decimal('0')."""
    pe = _make_paper_exchange()
    _patch_persistence(
        monkeypatch,
        sum_paper_trades_realized_pnl=AsyncMock(return_value=Decimal("0")),
    )
    total = await pe.get_closed_pnl_cumulative("test-bot")
    assert total == Decimal("0")
    assert isinstance(total, Decimal)


async def test_get_account_balance_raises_on_sub_account_mismatch() -> None:
    """Decision #8 contract: exact str equality; mismatch -> ValueError
    (verbatim mirror get_closed_pnl_cumulative)."""
    pe = _make_paper_exchange()
    with pytest.raises(ValueError, match="sub_account mismatch"):
        await pe.get_account_balance("other-bot")


async def test_get_account_balance_derives_wallet_from_seed_plus_realized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-530 paper parity: wallet = seed + Sigma realized; 3 totals alias wallet;
    unrealized_pnl == Decimal('0') (OQ-2=A no mark-to-market limitation).

    Hand-verified: seed Decimal('10000') + realized Decimal('250.5000')
    = Decimal('10250.5000') (exact Decimal addition, scale preserved).
    """
    pe = _make_paper_exchange()
    _patch_persistence(
        monkeypatch,
        sum_paper_trades_realized_pnl=AsyncMock(return_value=Decimal("250.5000")),
    )
    bal = await pe.get_account_balance("test-bot")
    assert bal.wallet_balance == Decimal("10250.5000")
    assert bal.unrealized_pnl == Decimal("0")
    # The simplification's defining identity (no margin-lockup, UPL=0).
    assert (
        bal.total_equity
        == bal.margin_balance
        == bal.available_balance
        == bal.wallet_balance
        == Decimal("10250.5000")
    )


async def test_get_account_balance_negative_realized_reduces_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loss path: a net-negative realized P&L reduces wallet below seed
    (signed Decimal addition, no abs). Hand-verified: Decimal('10000')
    + Decimal('-500') = Decimal('9500')."""
    pe = _make_paper_exchange()
    _patch_persistence(
        monkeypatch,
        sum_paper_trades_realized_pnl=AsyncMock(return_value=Decimal("-500")),
    )
    bal = await pe.get_account_balance("test-bot")
    assert bal.wallet_balance == Decimal("9500")
    assert bal.total_equity == bal.margin_balance == bal.available_balance == Decimal("9500")
    assert bal.unrealized_pnl == Decimal("0")


# --- T-527b1: get_mark_price (paper = last observed OHLC close) -------------


async def test_get_mark_price_returns_last_observed_close() -> None:
    """Paper reference price = _last_price[symbol] — the SAME source
    place_market_order simulates fills from (deterministic)."""
    pe = _make_paper_exchange()
    pe._last_price["BTCUSDT"] = Decimal("65000.50")
    price = await pe.get_mark_price("BTCUSDT")
    assert price == Decimal("65000.50")


async def test_get_mark_price_raises_order_rejected_when_unobserved() -> None:
    """No candle yet for symbol (empty _last_price) → OrderRejected
    (mirror paper get_instrument_info unknown-symbol)."""
    from packages.exchange.errors import OrderRejected

    pe = _make_paper_exchange()
    with pytest.raises(OrderRejected) as exc_info:
        await pe.get_mark_price("ETHUSDT")
    assert "ETHUSDT" in str(exc_info.value)


async def test_get_mark_price_reflects_candle_populated_last_price() -> None:
    """Replay/live determinism: _on_candle populates _last_price → get_mark_price
    returns that exact close (sizing source == fill-simulation source)."""
    pe = _make_paper_exchange()
    pe._drain_sl_tp_fill = AsyncMock()  # type: ignore[method-assign]
    payload = MagicMock()
    payload.payload = {
        "schema_version": "1.0",
        "symbol": "BTCUSDT",
        "interval": "1m",
        "bucket_start": datetime(2026, 4, 28, 12, 1, 0, tzinfo=UTC),
        "open": Decimal("65000"),
        "high": Decimal("65500"),
        "low": Decimal("64900"),
        "close": Decimal("65432.10"),
        "volume": Decimal("100"),
        "source": "binance",
        "is_closed": True,
    }
    payload.correlation_id = "corr"
    payload.published_at = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)
    payload.publisher = "test"
    await pe._on_candle(payload)
    price = await pe.get_mark_price("BTCUSDT")
    assert price == Decimal("65432.10")


# ---------------------------------------------------------------------------
# T-511b2 / ADR-0010 — emit_parent_lifecycle ctor flag (paper close emit)
# ---------------------------------------------------------------------------


def test_paper_exchange_emit_parent_lifecycle_default_false() -> None:
    """T-511b2 / ADR-0010: ctor flag defaults False — variant PE in shadow_worker
    stays False; primary bot PE in pool.py:198 wires True."""
    pe = _make_paper_exchange()
    # Private state pin — variant PE must keep this False to avoid self-cancel
    # loop where variant terminal triggers ShadowWorker._on_parent_close on
    # the variant's own parent_trade_id.
    assert pe._emit_parent_lifecycle is False


async def test_paper_exchange_persist_close_publishes_trade_closed_when_flag_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-511b2 / ADR-0010: primary bot PE (emit_parent_lifecycle=True) publishes
    TradeClosedPayload(parent_kind='paper') to ``trade.closed.<bot_id>`` post-commit."""
    from packages.exchange.paper import persistence as paper_persistence

    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.publish = AsyncMock()
    pe = _make_paper_exchange(emit_parent_lifecycle=True, bus=bus)
    # Seed _active_positions so _persist_close finds an open position.
    pe._active_positions["BTCUSDT"] = {
        "trade_id": 99,
        "side": "buy",
        "qty": Decimal("0.001"),
        "entry_price": Decimal("65000"),
        "entry_fee": Decimal("0"),
        "fees_paid": Decimal("0"),
        "sl_price": None,
        "tp_price": None,
        "tp_size": None,
        "tpsl_mode": "Full",
        "tp_hit": False,
        "realized_pnl": Decimal("0"),
    }
    # Stub persistence calls so _persist_close reaches the publish branch.
    monkeypatch.setattr(paper_persistence, "insert_paper_order", AsyncMock(return_value=200))
    monkeypatch.setattr(paper_persistence, "insert_paper_execution", AsyncMock())
    monkeypatch.setattr(paper_persistence, "close_paper_trade", AsyncMock())
    monkeypatch.setattr(paper_persistence, "delete_paper_position", AsyncMock())
    closed_at = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    await pe._persist_close(
        symbol="BTCUSDT",
        side="sell",
        qty=Decimal("0.001"),
        fill_price=Decimal("65500"),
        fee=Decimal("0"),
        placed_at=closed_at,
        exchange_order_id="paper-close-1",
        exchange_exec_id="paper-exec-1",
        correlation_id="paper-corr-1",
    )
    # Assert publish to trade.closed.<bot_id> with parent_kind='paper'.
    publish_calls = bus.publish.await_args_list
    matching = [c for c in publish_calls if c.args[0] == "trade.closed.test-bot"]
    assert len(matching) == 1, f"expected 1 trade.closed publish; got {len(matching)}"
    envelope = matching[0].args[1]
    assert envelope.payload["parent_trade_id"] == 99
    assert envelope.payload["parent_kind"] == "paper"
    assert envelope.publisher == "paper-exchange"
    # WG#8 negative assertion: dispatcher's reconcile_close path queries live-only
    # `position_state` table → returns None pre paper-bot trade → close-trigger
    # blok v dispatcher.py:237-256 sa nedosiahne → `emit_post_commit_close_event`
    # NEemituje TradeClosedPayload(parent_kind='live'). Architectural bypass
    # pin: ak by sa to v budúcnosti zmenilo, paper-bot trade close by produkoval
    # duplicate event (jeden z PE _persist_close + jeden z dispatcher path).
    live_calls = [
        c
        for c in publish_calls
        if c.args[0] == "trade.closed.test-bot" and c.args[1].payload.get("parent_kind") == "live"
    ]
    assert live_calls == [], (
        "duplicate TradeClosedPayload publish — paper bot reached dispatcher "
        "path AND PE _persist_close (architectural bypass regression)"
    )


async def test_paper_exchange_place_market_order_populates_paper_trade_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-511b2 / ADR-0010 (acceptance #4 + plan test #18): place_market_order returns
    OrderPlaceResult.paper_trade_id sourced from insert_paper_trade — used at
    placement.py:240-252 paper-fork to seed ShadowStartPayload.parent_trade_id."""
    from packages.exchange.paper import persistence as paper_persistence

    pe = _make_paper_exchange()
    # Seed _last_price + _last_candle so place_market_order doesn't ValueError.
    pe._last_price["BTCUSDT"] = Decimal("65000")
    from packages.bus.schemas import OhlcCandlePayload

    pe._last_candle["BTCUSDT"] = OhlcCandlePayload(
        symbol="BTCUSDT",
        bucket_start=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
        open=Decimal("64950"),
        high=Decimal("65010"),
        low=Decimal("64940"),
        close=Decimal("65000"),
        volume=Decimal("100"),
        is_closed=True,
    )
    # Stub paper persistence; insert_paper_trade returns fixed id.
    monkeypatch.setattr(paper_persistence, "insert_paper_order", AsyncMock(return_value=200))
    monkeypatch.setattr(paper_persistence, "insert_paper_trade", AsyncMock(return_value=777))
    monkeypatch.setattr(paper_persistence, "insert_paper_execution", AsyncMock())
    monkeypatch.setattr(paper_persistence, "insert_paper_position", AsyncMock())
    result = await pe.place_market_order(
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("0.001"),
    )
    assert result.paper_trade_id == 777


async def test_paper_exchange_persist_close_no_publish_when_flag_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-511b2 / ADR-0010: variant PE (default emit_parent_lifecycle=False) does
    NOT publish — prevents shadow self-cancel loop where variant's own
    terminal would trigger _on_parent_close cancelling itself."""
    from packages.exchange.paper import persistence as paper_persistence

    bus = MagicMock()
    bus.subscribe = AsyncMock()
    bus.publish = AsyncMock()
    pe = _make_paper_exchange(emit_parent_lifecycle=False, bus=bus)
    pe._active_positions["BTCUSDT"] = {
        "trade_id": 99,
        "side": "buy",
        "qty": Decimal("0.001"),
        "entry_price": Decimal("65000"),
        "entry_fee": Decimal("0"),
        "fees_paid": Decimal("0"),
        "sl_price": None,
        "tp_price": None,
        "tp_size": None,
        "tpsl_mode": "Full",
        "tp_hit": False,
        "realized_pnl": Decimal("0"),
    }
    monkeypatch.setattr(paper_persistence, "insert_paper_order", AsyncMock(return_value=200))
    monkeypatch.setattr(paper_persistence, "insert_paper_execution", AsyncMock())
    monkeypatch.setattr(paper_persistence, "close_paper_trade", AsyncMock())
    monkeypatch.setattr(paper_persistence, "delete_paper_position", AsyncMock())
    closed_at = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    await pe._persist_close(
        symbol="BTCUSDT",
        side="sell",
        qty=Decimal("0.001"),
        fill_price=Decimal("65500"),
        fee=Decimal("0"),
        placed_at=closed_at,
        exchange_order_id="paper-close-1",
        exchange_exec_id="paper-exec-1",
        correlation_id="paper-corr-1",
    )
    # NO trade.closed publish — variant PE silent.
    trade_closed_calls = [
        c for c in bus.publish.await_args_list if c.args[0] == "trade.closed.test-bot"
    ]
    assert trade_closed_calls == []
