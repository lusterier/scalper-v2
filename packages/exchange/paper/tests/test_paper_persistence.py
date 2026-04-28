"""§12.1 PaperExchange persistence integration tests (T-213b).

§N4 TDD step 2 per plan-doc. Throwaway DB via ``migrated_db_dsn``
fixture; mirror T-202/T-203/T-208 + 0008 patterns. Env-gated
POSTGRES_TEST_DSN; skipped locally; pass on ci-full.

Tests cover:

* OPEN flow — single-tx INSERT chain across paper_orders + paper_trades +
  paper_executions + paper_positions; round-trip Decimal exactness;
  exchange='paper' on paper_orders; idempotent=False on market orders.
* CLOSE flow (reduce_only=True) — UPDATE paper_trades close + DELETE
  paper_positions + insert close paper_orders + paper_executions; Hand
  verification §E.1 realized_pnl + notional_usd.
* OrderRejected guards (open with existing position; close without one).
* set_trading_stop — UPDATE paper_positions sl_price + tp_price ONLY
  (BLOCKER 1 schema parity); tpsl_mode + tp_size remain in dict.
* SL cross drain — synthetic paper_orders SL + paper_executions + close
  paper_trades + delete paper_positions; H-024 invariant.
* Partial TP drain — paper_trades stays OPEN with reduced qty; tp_hit=TRUE.
* cancel_order — UPDATE paper_orders status='cancelled'.
* CONCERN 4 invariant — _active_positions ↔ paper_positions parity.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from packages.bus import MessageEnvelope
from packages.core import BotId, CorrelationId
from packages.exchange.errors import OrderRejected
from packages.exchange.paper import PaperExchange

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _make_envelope(
    *,
    symbol: str = "BTCUSDT",
    open_: Decimal = Decimal("65000"),
    high: Decimal = Decimal("65100"),
    low: Decimal = Decimal("64900"),
    close: Decimal = Decimal("65000"),
    is_closed: bool = True,
) -> MessageEnvelope:
    payload = {
        "schema_version": "1.0",
        "symbol": symbol,
        "interval": "1m",
        "bucket_start": datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": Decimal("100"),
        "source": "binance",
        "is_closed": is_closed,
    }
    return MessageEnvelope(
        correlation_id=CorrelationId("corr-t213b"),
        published_at=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
        publisher="test-suite",
        payload=payload,
    )


async def _seed_bot(conn: object, bot_id: str) -> None:
    """Insert a bots row so paper_orders.bot_id FK passes.

    Accepts either ``asyncpg.Connection`` or ``PoolConnectionProxy``;
    asyncpg-stubs splits these but the .execute() surface is shared.
    """
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO bots (bot_id, display_name, created_at, status, "
        "exchange_mode, config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, 'active', 'paper', 'sha256:test', $4)",
        bot_id,
        f"T-213b smoke {bot_id}",
        datetime(2026, 4, 28, tzinfo=UTC),
        datetime(2026, 4, 28, tzinfo=UTC),
    )


@pytest.fixture
async def paper_exchange(
    migrated_db_dsn: str,
) -> AsyncIterator[tuple[PaperExchange, asyncpg.Pool, str]]:
    """Build a real PaperExchange against a throwaway migrated DB."""
    bot_id = f"test_t213b_{uuid.uuid4().hex[:8]}"
    pool = await asyncpg.create_pool(dsn=migrated_db_dsn, min_size=1, max_size=2)
    assert pool is not None

    async def _init(conn: asyncpg.Connection) -> None:
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    # Re-create with init to register jsonb codec.
    await pool.close()
    pool = await asyncpg.create_pool(dsn=migrated_db_dsn, min_size=1, max_size=2, init=_init)
    assert pool is not None

    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)

    bus = MagicMock()
    bus.subscribe = AsyncMock()
    fixed_now = datetime(2026, 4, 28, 12, 5, 0, tzinfo=UTC)
    pe = PaperExchange(
        seed_balance=Decimal("10000"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
        bot_id=BotId(bot_id),
        bus=bus,
        slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
        now_fn=lambda: fixed_now,
        pool=pool,
    )
    try:
        yield pe, pool, bot_id
    finally:
        await pool.close()


# --- OPEN flow --------------------------------------------------------------


async def test_place_market_order_open_persists_full_chain(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """OPEN flow: paper_orders + paper_trades + paper_executions + paper_positions all populated.

    Hand verification §E.1: notional_usd = 0.5 * 65000 = 32500.0000
    (entry test fixture uses zero-slippage to keep math readable; production
    place_market_order applies T-213a §C slippage so notional_usd reflects
    fill_price post-slippage — see plan-doc Decision #3).
    """
    pe, pool, bot_id = paper_exchange
    # No-slippage params for clean Hand verification §E.1.
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    result = await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    assert result.exchange_order_id.startswith("paper-")

    async with pool.acquire() as conn:
        order = await conn.fetchrow("SELECT * FROM paper_orders WHERE bot_id = $1", bot_id)
        assert order is not None
        assert order["exchange"] == "paper"
        assert order["order_type"] == "market"
        assert order["status"] == "filled"
        assert order["idempotent"] is False  # Decision #3 mapping
        assert order["qty"] == Decimal("0.5")
        assert order["price"] == Decimal("65000")

        trade = await conn.fetchrow("SELECT * FROM paper_trades WHERE bot_id = $1", bot_id)
        assert trade is not None
        assert trade["status"] == "open"
        assert trade["entry_price"] == Decimal("65000")
        assert trade["qty"] == Decimal("0.5")
        assert trade["notional_usd"] == Decimal("32500.0000")  # BLOCKER 3
        assert trade["fees_paid"] == Decimal("19.5000")  # 0.5 * 65000 * 0.0006

        execution = await conn.fetchrow("SELECT * FROM paper_executions WHERE bot_id = $1", bot_id)
        assert execution is not None
        assert execution["exec_type"] == "open"
        assert execution["price"] == Decimal("65000")

        position = await conn.fetchrow("SELECT * FROM paper_positions WHERE bot_id = $1", bot_id)
        assert position is not None
        assert position["side"] == "buy"
        assert position["qty"] == Decimal("0.5")
        assert position["remaining_qty"] == Decimal("0.5")


async def test_place_market_order_open_emits_execution_and_position_events(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Decision #2: persist-then-emit; events on queue post-commit."""
    pe, _pool, _bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    exec_event = await pe._execution_queue.get()
    pos_event = await pe._position_queue.get()
    assert exec_event.symbol == "BTCUSDT"
    assert exec_event.side == "buy"
    assert exec_event.price == Decimal("65000")
    assert pos_event.size == Decimal("0.5")
    assert pos_event.entry_price == Decimal("65000")


async def test_place_market_order_open_with_existing_position_raises(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Decision #7 / BLOCKER 4: reduce_only=False with active position raises OrderRejected."""
    pe, _pool, _bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    with pytest.raises(OrderRejected, match="position_already_open"):
        await pe.place_market_order("BTCUSDT", "buy", Decimal("0.3"))


async def test_place_market_order_close_without_position_raises(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Symmetric guard: reduce_only=True without position raises OrderRejected."""
    pe, _pool, _bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    with pytest.raises(OrderRejected, match="no_position_to_close"):
        await pe.place_market_order("BTCUSDT", "sell", Decimal("0.5"), reduce_only=True)


# --- CLOSE flow (reduce_only=True) ------------------------------------------


async def test_place_market_order_close_finalises_paper_trades_and_clears_paper_positions(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Hand verification §E.1: realized_pnl = 460.7000 on full close."""
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    # Open buy at 65000.
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    # Drain emit queues so close test sees fresh events.
    _ = await pe._execution_queue.get()
    _ = await pe._position_queue.get()
    # Close at 66000 — price moves up; long realizes profit.
    await pe._on_candle(
        _make_envelope(close=Decimal("66000"), high=Decimal("66100"), low=Decimal("65900"))
    )
    await pe.place_market_order("BTCUSDT", "sell", Decimal("0.5"), reduce_only=True)

    async with pool.acquire() as conn:
        trade = await conn.fetchrow("SELECT * FROM paper_trades WHERE bot_id = $1", bot_id)
        assert trade is not None
        assert trade["status"] == "closed"
        assert trade["close_reason"] == "manual"
        assert trade["exit_price"] == Decimal("66000")
        assert trade["realized_pnl"] == Decimal("460.7000")  # §E.1
        assert trade["fees_paid"] == Decimal("39.3000")  # §E.1

        position = await conn.fetchrow("SELECT * FROM paper_positions WHERE bot_id = $1", bot_id)
        assert position is None  # paper_positions DELETED on full close

        # Two executions: open + close.
        execs = await conn.fetch(
            "SELECT * FROM paper_executions WHERE bot_id = $1 ORDER BY executed_at",
            bot_id,
        )
        assert len(execs) == 2
        assert execs[0]["exec_type"] == "open"
        assert execs[1]["exec_type"] == "close"


# --- set_trading_stop -------------------------------------------------------


async def test_set_trading_stop_persists_sl_price_and_tp_price_only(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """BLOCKER 1: paper_positions has only sl_price + tp_price; tpsl_mode + tp_size in dict."""
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    await pe.set_trading_stop(
        "BTCUSDT",
        "Partial",
        sl_price=Decimal("64500"),
        tp_price=Decimal("65500"),
        tp_size=Decimal("0.1"),
    )

    async with pool.acquire() as conn:
        position = await conn.fetchrow(
            "SELECT sl_price, tp_price FROM paper_positions WHERE bot_id = $1",
            bot_id,
        )
        assert position is not None
        assert position["sl_price"] == Decimal("64500")
        assert position["tp_price"] == Decimal("65500")

    # tpsl_mode + tp_size in dict only (no DB columns).
    state = pe._active_positions["BTCUSDT"]
    assert state["tpsl_mode"] == "Partial"
    assert state["tp_size"] == Decimal("0.1")


# --- SL/TP drain ------------------------------------------------------------


async def test_sl_cross_drain_writes_synthetic_order_and_closes_paper_trades(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """H-024: paper_executions.order_id resolves via FK to a real synthetic paper_orders SL row."""
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    await pe.set_trading_stop(
        "BTCUSDT",
        "Full",
        sl_price=Decimal("64500"),
        tp_price=Decimal("65500"),
    )
    # Drain queues from open.
    _ = await pe._execution_queue.get()
    _ = await pe._position_queue.get()
    # Trigger SL via candle that crosses 64500.
    sl_candle = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65050"),
        low=Decimal("64400"),
        close=Decimal("64600"),
    )
    await pe._on_candle(sl_candle)

    async with pool.acquire() as conn:
        # Synthetic SL order created.
        sl_order = await conn.fetchrow(
            "SELECT * FROM paper_orders WHERE bot_id = $1 AND order_type = 'sl'",
            bot_id,
        )
        assert sl_order is not None
        assert sl_order["price"] == Decimal("64500")
        assert sl_order["status"] == "filled"
        assert sl_order["idempotent"] is True  # Decision #3 mapping

        # paper_executions row references SL order via FK.
        sl_exec = await conn.fetchrow(
            "SELECT * FROM paper_executions WHERE order_id = $1", sl_order["id"]
        )
        assert sl_exec is not None
        assert sl_exec["exec_type"] == "sl"

        # paper_trades closed.
        trade = await conn.fetchrow("SELECT * FROM paper_trades WHERE bot_id = $1", bot_id)
        assert trade is not None
        assert trade["status"] == "closed"
        assert trade["close_reason"] == "sl"

        # paper_positions deleted on full close.
        position = await conn.fetchrow("SELECT * FROM paper_positions WHERE bot_id = $1", bot_id)
        assert position is None


async def test_partial_tp_drain_keeps_position_open_with_reduced_qty(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Decision #9 + Hand verification §E.2: partial TP — paper_trades stays open."""
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    await pe.set_trading_stop(
        "BTCUSDT",
        "Partial",
        sl_price=Decimal("64500"),
        tp_price=Decimal("65500"),
        tp_size=Decimal("0.1"),
    )
    _ = await pe._execution_queue.get()
    _ = await pe._position_queue.get()
    # TP cross.
    tp_candle = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65600"),
        low=Decimal("64900"),
        close=Decimal("65500"),
    )
    await pe._on_candle(tp_candle)

    async with pool.acquire() as conn:
        # paper_trades still OPEN with reduced qty.
        trade = await conn.fetchrow("SELECT * FROM paper_trades WHERE bot_id = $1", bot_id)
        assert trade is not None
        assert trade["status"] == "open"
        assert trade["qty"] == Decimal("0.4")  # 0.5 - 0.1
        # Hand verification §E.2: realized_pnl = 46.0700 (TP fee only;
        # entry fee reserved per OQ-3 default A).
        assert trade["realized_pnl"] == Decimal("46.0700")

        # paper_positions still present with tp_hit=TRUE + reduced remaining_qty.
        position = await conn.fetchrow("SELECT * FROM paper_positions WHERE bot_id = $1", bot_id)
        assert position is not None
        assert position["remaining_qty"] == Decimal("0.4")
        assert position["tp_hit"] is True


# --- cancel_order -----------------------------------------------------------


async def test_cancel_order_updates_paper_orders_status_to_cancelled(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))

    async with pool.acquire() as conn:
        order = await conn.fetchrow("SELECT id FROM paper_orders WHERE bot_id = $1", bot_id)
        assert order is not None
        order_id = order["id"]

    await pe.cancel_order("BTCUSDT", str(order_id))

    async with pool.acquire() as conn:
        cancelled = await conn.fetchrow("SELECT status FROM paper_orders WHERE id = $1", order_id)
        assert cancelled is not None
        assert cancelled["status"] == "cancelled"


# --- CONCERN 4 invariant ----------------------------------------------------


async def test_persist_failure_rolls_back_full_chain(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """§9.5 step 8 single-tx invariant: mid-tx failure → 0 paper_* rows persisted.

    Patch ``insert_paper_position`` to raise; tx context manager rolls back
    paper_orders + paper_trades + paper_executions inserts atomically.
    """
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))

    from packages.exchange.paper import persistence as persistence_module

    original_insert_position = persistence_module.insert_paper_position

    async def _broken_insert(*_args: object, **_kwargs: object) -> None:
        raise asyncpg.DataError("simulated mid-tx failure")

    persistence_module.insert_paper_position = _broken_insert
    try:
        with pytest.raises(asyncpg.DataError, match="simulated"):
            await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    finally:
        persistence_module.insert_paper_position = original_insert_position

    async with pool.acquire() as conn:
        orders = await conn.fetch("SELECT * FROM paper_orders WHERE bot_id = $1", bot_id)
        trades = await conn.fetch("SELECT * FROM paper_trades WHERE bot_id = $1", bot_id)
        executions = await conn.fetch("SELECT * FROM paper_executions WHERE bot_id = $1", bot_id)
        positions = await conn.fetch("SELECT * FROM paper_positions WHERE bot_id = $1", bot_id)
    assert len(orders) == 0
    assert len(trades) == 0
    assert len(executions) == 0
    assert len(positions) == 0


async def test_emit_happens_after_persist_commit(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Decision #2 ordering pin: queue.put runs AFTER tx commit, not during.

    If persist fails, queue stays empty (paired with rollback test above).
    """
    pe, _pool, _bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))

    from packages.exchange.paper import persistence as persistence_module

    original_insert_position = persistence_module.insert_paper_position

    async def _broken_insert(*_args: object, **_kwargs: object) -> None:
        raise asyncpg.DataError("simulated mid-tx failure")

    persistence_module.insert_paper_position = _broken_insert
    try:
        with pytest.raises(asyncpg.DataError):
            await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    finally:
        persistence_module.insert_paper_position = original_insert_position

    # Queues must NOT contain anything — emit must happen post-commit only.
    assert pe._execution_queue.empty()
    assert pe._position_queue.empty()


async def test_manual_reduce_only_close_after_partial_tp_uses_entry_fee_not_fees_paid(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Math-validator regression guard: manual close after partial TP must NOT
    double-subtract TP fee. Mirror §E.3 logic via reduce_only path:

    - Open 0.5 @ 65000 (entry_fee 19.5)
    - Partial TP: 0.1 @ 65500 (tp_fee 3.93) → partial_pnl 46.07, qty 0.4
    - Manual reduce_only close at 66000 (close_fee 15.84):
      full_close_pnl = (66000-65000)*0.4 - entry_fee(19.5) - close_fee(15.84) = 364.66
      aggregate = 364.66 + 46.07 = 410.73
    """
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    await pe.set_trading_stop(
        "BTCUSDT",
        "Partial",
        sl_price=Decimal("64500"),
        tp_price=Decimal("65500"),
        tp_size=Decimal("0.1"),
    )
    _ = await pe._execution_queue.get()
    _ = await pe._position_queue.get()
    # Partial TP fires.
    await pe._on_candle(
        _make_envelope(
            symbol="BTCUSDT",
            open_=Decimal("65000"),
            high=Decimal("65600"),
            low=Decimal("64900"),
            close=Decimal("65500"),
        )
    )
    _ = await pe._execution_queue.get()
    _ = await pe._position_queue.get()
    # Manual close on remaining 0.4 qty at 66000.
    await pe._on_candle(_make_envelope(close=Decimal("66000")))
    await pe.place_market_order("BTCUSDT", "sell", Decimal("0.4"), reduce_only=True)

    async with pool.acquire() as conn:
        trade = await conn.fetchrow(
            "SELECT realized_pnl, fees_paid, status, close_reason "
            "FROM paper_trades WHERE bot_id = $1",
            bot_id,
        )
    assert trade is not None
    assert trade["status"] == "closed"
    assert trade["close_reason"] == "manual"
    assert trade["realized_pnl"] == Decimal("410.7300")
    assert trade["fees_paid"] == Decimal("39.2700")  # 19.5 + 3.93 + 15.84


async def test_partial_tp_then_sl_close_yields_correct_aggregate_pnl(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Hand verification §E.3: partial TP + SL close yields aggregate -188.9100.

    Setup: open 0.5 BUY @ 65000 (entry_fee 19.5).
    Partial TP: 0.1 @ 65500 (tp_fee 3.93) → +46.07 partial pnl, qty=0.4.
    SL: 0.4 @ 64500 (sl_fee 15.48) → -200 - 19.5 - 15.48 = -234.98 sl_pnl.
    Aggregate: 46.07 + (-234.98) = -188.91.
    """
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    await pe.set_trading_stop(
        "BTCUSDT",
        "Partial",
        sl_price=Decimal("64500"),
        tp_price=Decimal("65500"),
        tp_size=Decimal("0.1"),
    )
    # Drain emission queues from open + set_trading_stop (single open emit).
    _ = await pe._execution_queue.get()
    _ = await pe._position_queue.get()
    # Partial TP fires.
    await pe._on_candle(
        _make_envelope(
            symbol="BTCUSDT",
            open_=Decimal("65000"),
            high=Decimal("65600"),
            low=Decimal("64900"),
            close=Decimal("65500"),
        )
    )
    _ = await pe._execution_queue.get()
    _ = await pe._position_queue.get()
    # SL fires on remaining 0.4 qty.
    await pe._on_candle(
        _make_envelope(
            symbol="BTCUSDT",
            open_=Decimal("65500"),
            high=Decimal("65600"),
            low=Decimal("64400"),
            close=Decimal("64600"),
        )
    )

    async with pool.acquire() as conn:
        trade = await conn.fetchrow(
            "SELECT realized_pnl, fees_paid, status, close_reason "
            "FROM paper_trades WHERE bot_id = $1",
            bot_id,
        )
    assert trade is not None
    assert trade["status"] == "closed"
    assert trade["close_reason"] == "sl"
    # §E.3 aggregate: -188.91.
    assert trade["realized_pnl"] == Decimal("-188.9100")
    # §E.3 fees_paid: 19.5 + 3.93 + 15.48 = 38.91.
    assert trade["fees_paid"] == Decimal("38.9100")


@pytest.mark.parametrize(
    ("variant", "expected_exec_type"),
    [
        ("open", "open"),
        ("close", "close"),
        ("sl", "sl"),
        ("tp", "tp"),
    ],
)
async def test_execution_event_shape_matches_protocol_dataclass(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
    variant: str,
    expected_exec_type: str,
) -> None:
    """§3.1 line 268 indistinguishability: ExecutionEvent fields populated for every variant.

    paper_executions.exec_type column must match the path: open / close / sl / tp.
    """
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))

    if variant == "close":
        await pe._on_candle(
            _make_envelope(close=Decimal("66000"), high=Decimal("66100"), low=Decimal("65900"))
        )
        await pe.place_market_order("BTCUSDT", "sell", Decimal("0.5"), reduce_only=True)
    elif variant == "sl":
        await pe.set_trading_stop("BTCUSDT", "Full", sl_price=Decimal("64500"))
        await pe._on_candle(
            _make_envelope(
                symbol="BTCUSDT",
                open_=Decimal("65000"),
                high=Decimal("65050"),
                low=Decimal("64400"),
                close=Decimal("64600"),
            )
        )
    elif variant == "tp":
        await pe.set_trading_stop("BTCUSDT", "Full", tp_price=Decimal("65500"))
        await pe._on_candle(
            _make_envelope(
                symbol="BTCUSDT",
                open_=Decimal("65000"),
                high=Decimal("65600"),
                low=Decimal("64900"),
                close=Decimal("65500"),
            )
        )

    async with pool.acquire() as conn:
        execs = await conn.fetch(
            "SELECT exec_type FROM paper_executions WHERE bot_id = $1 ORDER BY executed_at",
            bot_id,
        )
    exec_types = [row["exec_type"] for row in execs]
    assert expected_exec_type in exec_types


async def test_active_positions_dict_matches_paper_positions_after_each_mutation(
    paper_exchange: tuple[PaperExchange, asyncpg.Pool, str],
) -> None:
    """Decision #16 / CONCERN 4: in-memory dict ↔ DB row parity invariant."""
    pe, pool, bot_id = paper_exchange
    pe._slippage_params = {"fixed_slippage_pct": Decimal("0")}
    await pe._on_candle(_make_envelope(close=Decimal("65000")))

    # (1) After OPEN.
    await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT side, qty, remaining_qty, entry_price FROM paper_positions WHERE bot_id = $1",
            bot_id,
        )
    dict_state = pe._active_positions["BTCUSDT"]
    assert row is not None
    assert dict_state["side"] == row["side"]
    assert dict_state["qty"] == row["qty"]
    assert dict_state["entry_price"] == row["entry_price"]

    # (2) After set_trading_stop.
    await pe.set_trading_stop(
        "BTCUSDT",
        "Partial",
        sl_price=Decimal("64500"),
        tp_price=Decimal("65500"),
        tp_size=Decimal("0.1"),
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sl_price, tp_price FROM paper_positions WHERE bot_id = $1",
            bot_id,
        )
    dict_state = pe._active_positions["BTCUSDT"]
    assert row is not None
    assert dict_state["sl_price"] == row["sl_price"]
    assert dict_state["tp_price"] == row["tp_price"]

    # (3) After partial TP drain — qty/remaining_qty reduced.
    _ = await pe._execution_queue.get()
    _ = await pe._position_queue.get()
    tp_candle = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65600"),
        low=Decimal("64900"),
        close=Decimal("65500"),
    )
    await pe._on_candle(tp_candle)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT remaining_qty, tp_hit FROM paper_positions WHERE bot_id = $1",
            bot_id,
        )
    dict_state = pe._active_positions["BTCUSDT"]
    assert row is not None
    assert dict_state["qty"] == row["remaining_qty"]  # paper_positions.remaining_qty
    assert dict_state["tp_hit"] is row["tp_hit"]

    # (4) After full SL drain — paper_positions DELETED + dict entry removed.
    sl_candle = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65500"),
        high=Decimal("65600"),
        low=Decimal("64400"),
        close=Decimal("64600"),
    )
    await pe._on_candle(sl_candle)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM paper_positions WHERE bot_id = $1", bot_id)
    assert row is None
    assert "BTCUSDT" not in pe._active_positions
