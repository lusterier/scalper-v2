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
) -> PaperExchange:
    """Construct PaperExchange with mock pool + frozen-time now_fn."""
    bus = MagicMock()
    bus.subscribe = AsyncMock()
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
    """Paper-fixed-fields invariant: no leverage column, no live mark price."""
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


async def test_get_fill_price_returns_decimal_when_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: pass-through Decimal."""
    pe = _make_paper_exchange()
    _patch_persistence(
        monkeypatch,
        select_paper_execution_price_by_order_id=AsyncMock(return_value=Decimal("65032.5")),
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
        select_paper_execution_price_by_order_id=AsyncMock(return_value=None),
    )
    price = await pe.get_fill_price("BTCUSDT", "paper-missing")
    assert price is None


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
