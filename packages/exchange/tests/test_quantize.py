"""§N4 unit tests for :mod:`packages.exchange.quantize` (T-529 / H-036)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from packages.exchange.errors import QtyValidationError
from packages.exchange.quantize import quantize_qty
from packages.exchange.types import InstrumentInfo


def _info(
    *,
    qty_step: Decimal = Decimal("0.001"),
    min_order_qty: Decimal = Decimal("0.001"),
    min_notional_usd: Decimal = Decimal("5"),
    symbol: str = "BTCUSDT",
) -> InstrumentInfo:
    return InstrumentInfo(
        symbol=symbol,
        qty_step=qty_step,
        min_order_qty=min_order_qty,
        min_notional_usd=min_notional_usd,
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
