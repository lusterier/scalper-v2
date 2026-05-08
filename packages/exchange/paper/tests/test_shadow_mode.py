"""T-511a PaperExchange shadow-mode prereq tests (5 unit tests).

Per ADR-0005 v2: partial_tp promotes sl_type from 'protective' (or 'be') to 'trail'.
PE 3-state vocabulary: 'protective' (initial post-entry) / 'be' (lifecycle BE-trigger,
NOT in PE today) / 'trail' (post-partial_tp).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from packages.core import BotId
from packages.exchange.paper import PaperExchange


def _make_pool() -> MagicMock:
    """Mock asyncpg.Pool stand-in for PaperExchange constructor."""
    pool = MagicMock()
    pool.close = AsyncMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=[])
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=conn)
    tx_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx_cm)
    pool_cm = MagicMock()
    pool_cm.__aenter__ = AsyncMock(return_value=conn)
    pool_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=pool_cm)
    return pool


def _make_pe(*, seed_open_state: dict[str, object] | None = None) -> PaperExchange:
    bus = MagicMock()
    # Mock subscription with `active` attribute only (mirrors ReplaySubscription
    # surface; T-511a bus_unsubscribe falls through hasattr 'unsubscribe' check).
    sub = MagicMock(spec=["active"])
    sub.active = True
    bus.subscribe = AsyncMock(return_value=sub)
    return PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test-shadow"),
        bus=bus,
        slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
        pool=_make_pool(),
        seed_open_state=seed_open_state,
    )


_SEED_BTC = {
    "symbol": "BTCUSDT",
    "side": "buy",
    "qty": Decimal("0.5"),
    "entry_price": Decimal("65000"),
    "sl_price": Decimal("64000"),
    "tp_price": Decimal("66000"),
    "trade_id": 42,
}


async def test_seed_open_state_populates_caches_before_subscribe() -> None:
    """T-511a: seed_open_state pre-populates _active_positions + _last_price + _last_candle."""
    pe = _make_pe(seed_open_state=_SEED_BTC)
    await pe.start_consuming()
    assert "BTCUSDT" in pe._active_positions
    pos = pe._active_positions["BTCUSDT"]
    assert pos["trade_id"] == 42
    assert pos["side"] == "buy"
    assert pos["qty"] == Decimal("0.5")
    assert pos["entry_price"] == Decimal("65000")
    assert pos["sl_type"] == "protective"  # default initial
    assert pe._last_price["BTCUSDT"] == Decimal("65000")
    cached = pe._last_candle["BTCUSDT"]
    assert cached.open == Decimal("65000")
    assert cached.high == Decimal("65000")
    assert cached.low == Decimal("65000")
    assert cached.close == Decimal("65000")
    assert cached.is_closed is True


async def test_seed_open_state_default_none_preserves_live_mode() -> None:
    """T-511a backwards-compat: no seed → empty caches after start_consuming (live unchanged)."""
    pe = _make_pe(seed_open_state=None)
    await pe.start_consuming()
    # _hydrate_active_positions runs (queries paper_positions; mock returns []).
    assert pe._active_positions == {}
    assert pe._last_price == {}
    assert pe._last_candle == {}


async def test_bus_unsubscribe_market_ohlc_idempotent() -> None:
    """T-511a: bus_unsubscribe_market_ohlc is idempotent (no exception on second call)."""
    pe = _make_pe()
    await pe.start_consuming()
    assert pe._market_ohlc_subscription is not None
    await pe.bus_unsubscribe_market_ohlc()
    assert pe._market_ohlc_subscription is None
    # Second call: no-op.
    await pe.bus_unsubscribe_market_ohlc()
    assert pe._market_ohlc_subscription is None


async def test_set_trading_stop_initializes_sl_type_protective() -> None:
    """T-511a: set_trading_stop initializes sl_type='protective' if missing."""
    pe = _make_pe()
    # Manually inject minimal position dict (no sl_type yet).
    pe._active_positions["BTCUSDT"] = {
        "trade_id": 1,
        "side": "buy",
        "qty": Decimal("1"),
        "entry_price": Decimal("65000"),
        "entry_fee": Decimal("0"),
        "fees_paid": Decimal("0"),
    }
    await pe.set_trading_stop(
        "BTCUSDT",
        "Full",
        sl_price=Decimal("64000"),
        tp_price=Decimal("66000"),
    )
    assert pe._active_positions["BTCUSDT"]["sl_type"] == "protective"


async def test_drain_partial_tp_promotes_sl_type_to_trail() -> None:
    """T-511a + ADR-0005 v2: _drain_partial_tp promotes sl_type 'protective' → 'trail'."""
    from packages.exchange.paper.adapter import PendingSLTPFill

    pe = _make_pe(seed_open_state=_SEED_BTC)
    await pe.start_consuming()
    # Verify pre-drain state.
    assert pe._active_positions["BTCUSDT"]["sl_type"] == "protective"
    # Configure partial mode (tpsl_mode='Partial' with tp_size=0.2 → partial_tp on TP cross).
    pe._active_positions["BTCUSDT"]["tpsl_mode"] = "Partial"
    pe._active_positions["BTCUSDT"]["tp_size"] = Decimal("0.2")
    pe._active_positions["BTCUSDT"]["realized_pnl"] = Decimal("0.0000")
    # Trigger _drain_partial_tp directly with synthetic fill.
    fill = PendingSLTPFill(
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("0.2"),
        trigger_price=Decimal("66000"),
        triggered_at=datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC),
        kind="tp",
        tpsl_mode="Partial",
    )
    await pe._drain_partial_tp(fill)
    # Per ADR-0005 v2: sl_type promoted to 'trail' (NOT 'be').
    assert pe._active_positions["BTCUSDT"]["sl_type"] == "trail"
