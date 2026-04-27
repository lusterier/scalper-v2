"""§12.1 PaperExchange fill semantics — T-213a integration tests.

§N4 TDD discipline: tests written FIRST per operator-locked
implementation order. Hand verification §C (market-order fill price)
and §D (SL/TP cross detection) in docs/plans/T-213.md.

Tests exercise:

* Constructor extension (bot_id, bus, slippage_params, now_fn DI).
* Slippage params validation (Decision #11 — allow-list per model).
* `place_market_order` partial-body computation (Decision #9 +
  Hand verification §C); raises NotImplementedError pointing at T-213b.
* `set_trading_stop` partial-body registration in active-positions
  dict (Decision #14 — H-013 tpsl_mode propagation); raises
  NotImplementedError pointing at T-213b.
* `_on_candle` last-price cache update (Decision #17).
* `_check_sl_tp_crosses` SL/TP cross detection per Hand verification §D
  with PendingSLTPFill enqueue (Decision #12 + #13).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.bus import MessageEnvelope
from packages.core import BotId, CorrelationId
from packages.exchange.paper import PaperExchange
from packages.exchange.paper.adapter import PendingSLTPFill


def _make_envelope(
    *,
    symbol: str,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    is_closed: bool = True,
    bucket_start: datetime | None = None,
) -> MessageEnvelope:
    """Construct a MessageEnvelope wrapping an OhlcCandlePayload-shaped dict."""
    payload = {
        "schema_version": "1.0",
        "symbol": symbol,
        "interval": "1m",
        "bucket_start": (bucket_start or datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": Decimal("100"),
        "source": "binance",
        "is_closed": is_closed,
    }
    return MessageEnvelope(
        correlation_id=CorrelationId("corr-t213a"),
        published_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
        publisher="test-suite",
        payload=payload,
    )


def _make_paper_exchange(
    *,
    slippage_model: str = "fixed_pct",
    slippage_params: dict[str, Decimal] | None = None,
    fee_rate: Decimal = Decimal("0.0006"),
    now: datetime | None = None,
) -> PaperExchange:
    """Construct a PaperExchange with mock NATS bus + frozen-time now_fn."""
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    fixed_now = now or datetime(2026, 4, 27, 12, 5, 0, tzinfo=UTC)
    return PaperExchange(
        seed_balance=Decimal("10000"),
        slippage_model=slippage_model,  # type: ignore[arg-type]
        fee_rate=fee_rate,
        bot_id=BotId("test-bot"),
        bus=bus,
        slippage_params=slippage_params or {"fixed_slippage_pct": Decimal("0.0005")},
        now_fn=lambda: fixed_now,
    )


# --- Constructor extension + slippage params validation ---------------------


def test_constructor_accepts_each_model_with_correct_keys() -> None:
    """Each of 3 slippage models accepts its required key set."""
    for model, key in (
        ("fixed_pct", "fixed_slippage_pct"),
        ("proportional_to_qty", "qty_slippage_coeff"),
        ("half_spread", "half_spread_factor"),
    ):
        pe = _make_paper_exchange(
            slippage_model=model,
            slippage_params={key: Decimal("0.0005")},
        )
        assert pe._slippage_model == model


def test_constructor_rejects_missing_coefficient_key() -> None:
    """slippage_params missing required key raises ValueError."""
    with pytest.raises(ValueError, match="must have keys"):
        _make_paper_exchange(
            slippage_model="fixed_pct",
            slippage_params={"qty_slippage_coeff": Decimal("0.0001")},  # wrong key
        )


def test_constructor_rejects_extra_coefficient_key_for_chosen_model() -> None:
    """slippage_params with extra key for chosen model raises ValueError."""
    with pytest.raises(ValueError, match="must have keys"):
        _make_paper_exchange(
            slippage_model="fixed_pct",
            slippage_params={
                "fixed_slippage_pct": Decimal("0.0005"),
                "qty_slippage_coeff": Decimal("0.0001"),  # extra
            },
        )


# --- _on_candle last-price cache update -------------------------------------


async def test_on_candle_updates_last_price_cache() -> None:
    """Closed candle updates `_last_price[symbol]` to candle.close."""
    pe = _make_paper_exchange()
    envelope = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65100"),
        low=Decimal("64900"),
        close=Decimal("65050"),
    )
    await pe._on_candle(envelope)
    assert pe._last_price["BTCUSDT"] == Decimal("65050")


async def test_on_candle_in_progress_candle_ignored() -> None:
    """Only is_closed=True candles update last_price."""
    pe = _make_paper_exchange()
    envelope = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65100"),
        low=Decimal("64900"),
        close=Decimal("65050"),
        is_closed=False,
    )
    await pe._on_candle(envelope)
    assert "BTCUSDT" not in pe._last_price


# --- place_market_order partial body (Hand verification §C) -----------------


async def test_place_market_order_buy_uses_last_close_plus_slippage() -> None:
    """Hand verification §C.1 buy: 65000 + 32.5 = 65032.5."""
    pe = _make_paper_exchange()
    envelope = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65100"),
        low=Decimal("64900"),
        close=Decimal("65000"),
    )
    await pe._on_candle(envelope)
    with pytest.raises(NotImplementedError) as info:
        await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    msg = str(info.value)
    assert "T-213b" in msg
    assert "65032.5" in msg or "32.5" in msg  # fill_price or slippage in message


async def test_place_market_order_sell_uses_last_close_minus_slippage() -> None:
    """Hand verification §C.2 sell: 65000 - 32.5 = 64967.5."""
    pe = _make_paper_exchange()
    envelope = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65100"),
        low=Decimal("64900"),
        close=Decimal("65000"),
    )
    await pe._on_candle(envelope)
    with pytest.raises(NotImplementedError) as info:
        await pe.place_market_order("BTCUSDT", "sell", Decimal("0.5"))
    msg = str(info.value)
    assert "T-213b" in msg
    assert "64967.5" in msg or "32.5" in msg


async def test_place_market_order_no_observed_price_raises() -> None:
    """No candle observed for symbol → raises with clear error."""
    pe = _make_paper_exchange()
    with pytest.raises(ValueError, match="No last-observed price"):
        await pe.place_market_order("BTCUSDT", "buy", Decimal("0.5"))


# --- set_trading_stop partial body + H-013 tpsl_mode propagation ------------


async def test_set_trading_stop_stores_tpsl_mode_in_active_positions() -> None:
    """Decision #14 / H-013: tpsl_mode propagated to active-positions dict."""
    pe = _make_paper_exchange()
    with pytest.raises(NotImplementedError) as info:
        await pe.set_trading_stop(
            "BTCUSDT",
            "Partial",
            sl_price=Decimal("64500"),
            tp_price=Decimal("65500"),
            tp_size=Decimal("0.1"),
        )
    assert "T-213b" in str(info.value)
    state = pe._active_positions["BTCUSDT"]
    assert state["tpsl_mode"] == "Partial"
    assert state["sl_price"] == Decimal("64500")
    assert state["tp_price"] == Decimal("65500")
    assert state["tp_size"] == Decimal("0.1")


# --- _check_sl_tp_crosses (Hand verification §D) ----------------------------


async def _seed_buy_position(
    pe: PaperExchange,
    *,
    symbol: str = "BTCUSDT",
    qty: Decimal = Decimal("0.5"),
    sl_price: Decimal = Decimal("64500"),
    tp_price: Decimal = Decimal("65500"),
    tpsl_mode: str = "Full",
) -> None:
    """Inject an active buy position (T-213a state; T-213b will hydrate from DB)."""
    pe._active_positions[symbol] = {
        "side": "buy",
        "qty": qty,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "tp_size": qty,
        "tpsl_mode": tpsl_mode,
    }


async def test_sl_cross_detection_buy_position() -> None:
    """Hand verification §D.1: low=64400 ≤ sl=64500 ≤ high=65050 → SL fill."""
    pe = _make_paper_exchange()
    await _seed_buy_position(pe)
    candle_env = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65050"),
        low=Decimal("64400"),
        close=Decimal("64600"),
    )
    await pe._on_candle(candle_env)
    assert len(pe._pending_sl_tp_fills) == 1
    fill = pe._pending_sl_tp_fills[0]
    assert fill.kind == "sl"
    assert fill.trigger_price == Decimal("64500")
    assert fill.tpsl_mode == "Full"
    assert fill.qty == Decimal("0.5")


async def test_tp_cross_detection_buy_position() -> None:
    """Hand verification §D.2: tp=65500 in [64900, 65600] → TP fill."""
    pe = _make_paper_exchange()
    await _seed_buy_position(pe)
    candle_env = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65600"),
        low=Decimal("64900"),
        close=Decimal("65500"),
    )
    await pe._on_candle(candle_env)
    assert len(pe._pending_sl_tp_fills) == 1
    fill = pe._pending_sl_tp_fills[0]
    assert fill.kind == "tp"
    assert fill.trigger_price == Decimal("65500")


async def test_sl_and_tp_both_cross_pessimistic_sl_first() -> None:
    """Hand verification §D.3: both SL+TP cross same candle → SL-first only."""
    pe = _make_paper_exchange()
    await _seed_buy_position(pe)
    candle_env = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65700"),
        low=Decimal("64300"),
        close=Decimal("64600"),
    )
    await pe._on_candle(candle_env)
    # Only ONE fill enqueued (SL-first per Q4-A pessimistic).
    assert len(pe._pending_sl_tp_fills) == 1
    assert pe._pending_sl_tp_fills[0].kind == "sl"
    assert pe._pending_sl_tp_fills[0].trigger_price == Decimal("64500")


async def test_sell_position_sl_cross_inverted() -> None:
    """Hand verification §D.4: sell position SL ABOVE entry; TP BELOW."""
    pe = _make_paper_exchange()
    pe._active_positions["BTCUSDT"] = {
        "side": "sell",
        "qty": Decimal("0.3"),
        "sl_price": Decimal("65500"),
        "tp_price": Decimal("64500"),
        "tp_size": Decimal("0.1"),
        "tpsl_mode": "Partial",
    }
    candle_env = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65600"),
        low=Decimal("64900"),
        close=Decimal("65500"),
    )
    await pe._on_candle(candle_env)
    assert len(pe._pending_sl_tp_fills) == 1
    fill = pe._pending_sl_tp_fills[0]
    assert fill.kind == "sl"
    assert fill.side == "sell"
    assert fill.trigger_price == Decimal("65500")
    assert fill.tpsl_mode == "Partial"  # H-013 propagation
    assert fill.qty == Decimal("0.3")


async def test_check_sl_tp_crosses_propagates_tpsl_mode_from_set_trading_stop() -> None:
    """H-013 invariant: tpsl_mode flows from set_trading_stop → active_positions
    → PendingSLTPFill without 'Full' default baking."""
    pe = _make_paper_exchange()
    # Register Partial via set_trading_stop (raises but stores state).
    with pytest.raises(NotImplementedError):
        await pe.set_trading_stop(
            "BTCUSDT",
            "Partial",
            sl_price=Decimal("64500"),
            tp_price=Decimal("65500"),
            tp_size=Decimal("0.1"),
        )
    # Inject side+qty (T-213b sets these via place_market_order persistence;
    # at T-213a we patch directly).
    pe._active_positions["BTCUSDT"]["side"] = "buy"
    pe._active_positions["BTCUSDT"]["qty"] = Decimal("0.5")
    # Trigger SL.
    candle_env = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65050"),
        low=Decimal("64400"),
        close=Decimal("64600"),
    )
    await pe._on_candle(candle_env)
    fill = pe._pending_sl_tp_fills[0]
    assert fill.tpsl_mode == "Partial", (
        f"Expected Partial propagated from set_trading_stop; got {fill.tpsl_mode}"
    )


async def test_pending_fill_carries_triggered_at_from_now_fn() -> None:
    """Decision #13: triggered_at captured via injected now_fn for determinism."""
    fixed_now = datetime(2026, 4, 27, 13, 0, 0, tzinfo=UTC)
    pe = _make_paper_exchange(now=fixed_now)
    await _seed_buy_position(pe)
    candle_env = _make_envelope(
        symbol="BTCUSDT",
        open_=Decimal("65000"),
        high=Decimal("65050"),
        low=Decimal("64400"),
        close=Decimal("64600"),
    )
    await pe._on_candle(candle_env)
    assert pe._pending_sl_tp_fills[0].triggered_at == fixed_now


# --- PendingSLTPFill dataclass shape -----------------------------------------


def test_pending_sl_tp_fill_is_frozen_and_slotted() -> None:
    """Decision #12: PendingSLTPFill is immutable + slot-optimised."""
    fill = PendingSLTPFill(
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("0.5"),
        trigger_price=Decimal("64500"),
        triggered_at=datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
        kind="sl",
        tpsl_mode="Full",
    )
    assert not hasattr(fill, "__dict__")  # slots
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        fill.kind = "tp"  # type: ignore[misc]


# --- start_consuming subscribes to market.ohlc.1m.> -------------------------


async def test_start_consuming_subscribes_to_ohlc_wildcard() -> None:
    """Decision #16: start_consuming subscribes once to market.ohlc.1m.>."""
    pe = _make_paper_exchange()
    await pe.start_consuming()
    subscribe = pe._bus.subscribe
    assert isinstance(subscribe, AsyncMock)
    subscribe.assert_awaited_once()
    assert subscribe.await_args is not None
    args, _kwargs = subscribe.await_args
    assert args[0] == "market.ohlc.1m.>"
