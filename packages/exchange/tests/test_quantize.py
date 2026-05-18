"""§N4 unit tests for :mod:`packages.exchange.quantize` (T-529 / H-036)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from packages.exchange.errors import QtyValidationError
from packages.exchange.quantize import quantize_price, quantize_qty
from packages.exchange.types import InstrumentInfo


def _info(
    *,
    qty_step: Decimal = Decimal("0.001"),
    min_order_qty: Decimal = Decimal("0.001"),
    min_notional_usd: Decimal = Decimal("5"),
    tick_size: Decimal = Decimal("0.1"),
    symbol: str = "BTCUSDT",
) -> InstrumentInfo:
    return InstrumentInfo(
        symbol=symbol,
        qty_step=qty_step,
        min_order_qty=min_order_qty,
        min_notional_usd=min_notional_usd,
        tick_size=tick_size,
    )


def test_quantize_qty_returns_input_unchanged_when_already_aligned() -> None:
    """Aligned qty (multiple of qty_step) returns unchanged."""
    out = quantize_qty(Decimal("0.001"), _info())
    assert out == Decimal("0.001")


def test_quantize_qty_rounds_down_to_qty_step() -> None:
    """T-529 / H-036 hand-fixture: 0.0015 floor-div 0.001 = 1; 1 * 0.001 = 0.001 exact."""
    out = quantize_qty(Decimal("0.0015"), _info())
    assert out == Decimal("0.001")


def test_quantize_qty_raises_when_qty_below_min_order_qty() -> None:
    """Pre-round: qty < min_order_qty → QtyValidationError(min_order_qty)."""
    with pytest.raises(QtyValidationError) as exc_info:
        quantize_qty(Decimal("0.0005"), _info())
    assert exc_info.value.constraint == "min_order_qty"
    assert exc_info.value.actual_qty == Decimal("0.0005")
    assert exc_info.value.symbol == "BTCUSDT"


def test_quantize_qty_raises_when_rounded_below_min_order_qty() -> None:
    """Post-round: 0.0009 // 0.001 = 0; 0 < min_order_qty → QtyValidationError."""
    with pytest.raises(QtyValidationError) as exc_info:
        quantize_qty(Decimal("0.0009"), _info())
    assert exc_info.value.constraint == "min_order_qty"
    assert exc_info.value.actual_qty == Decimal("0.0009")


# --- T-558a / finding #2: quantize_price (side-aware tick rounding) --------


def test_instrument_info_carries_tick_size() -> None:
    """T-558a — InstrumentInfo gains a required tick_size: Decimal field."""
    assert _info(tick_size=Decimal("0.1")).tick_size == Decimal("0.1")


def test_quantize_price_buy_floors_to_tick() -> None:
    """buy → ROUND_FLOOR: LONG SL down (never tighter), LONG TP down (never further)."""
    assert quantize_price(Decimal("76017.546"), "buy", Decimal("0.1")) == Decimal("76017.5")
    assert quantize_price(Decimal("77937.181"), "buy", Decimal("0.1")) == Decimal("77937.1")


def test_quantize_price_sell_ceils_to_tick() -> None:
    """sell → ROUND_CEILING: SHORT SL up (never tighter), SHORT TP up (never further)."""
    assert quantize_price(Decimal("76017.546"), "sell", Decimal("0.1")) == Decimal("76017.6")


def test_quantize_price_on_grid_unchanged_both_sides() -> None:
    """Price already on a tick boundary → unchanged for buy and sell."""
    assert quantize_price(Decimal("76017.5"), "buy", Decimal("0.1")) == Decimal("76017.5")
    assert quantize_price(Decimal("76017.5"), "sell", Decimal("0.1")) == Decimal("76017.5")


def test_quantize_price_tick_0_01() -> None:
    assert quantize_price(Decimal("100.007"), "buy", Decimal("0.01")) == Decimal("100.00")
    assert quantize_price(Decimal("100.007"), "sell", Decimal("0.01")) == Decimal("100.01")


def test_quantize_price_non_power_of_ten_tick() -> None:
    """tick 0.5 (not 10^-n): general grid alignment, not a decimal-exponent quantize."""
    assert quantize_price(Decimal("100.3"), "buy", Decimal("0.5")) == Decimal("100.0")
    assert quantize_price(Decimal("100.3"), "sell", Decimal("0.5")) == Decimal("100.5")


def test_quantize_price_raises_on_non_positive_tick() -> None:
    """tick <= 0 → ValueError (guards the T-558b apply-site)."""
    with pytest.raises(ValueError):
        quantize_price(Decimal("100"), "buy", Decimal("0"))
    with pytest.raises(ValueError):
        quantize_price(Decimal("100"), "sell", Decimal("-0.1"))
