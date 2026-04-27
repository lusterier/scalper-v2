"""§12.1 line 1937 slippage models — T-213a unit tests.

§N4 TDD discipline: tests written FIRST per operator-locked
implementation order. Hand verification §A in docs/plans/T-213.md
(MANDATORY for active math-validator gate; cross-checked verbatim).

Each model is a pure-functional kwarg-only function. No state, no I/O.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packages.exchange.paper import slippage

# --- fixed_pct ---------------------------------------------------------------


def test_fixed_pct_zero_pct_yields_zero_slippage() -> None:
    """Zero pct → zero slippage."""
    assert slippage.fixed_pct(price=Decimal("65000"), fixed_slippage_pct=Decimal("0")) == Decimal(
        "0"
    )


def test_fixed_pct_positive_pct_yields_proportional_slippage() -> None:
    """Hand verification §A.1: price=65000, pct=0.0005 → slippage=32.5000."""
    assert slippage.fixed_pct(
        price=Decimal("65000"), fixed_slippage_pct=Decimal("0.0005")
    ) == Decimal("32.5000")


def test_fixed_pct_scales_linearly_with_price() -> None:
    """price * fixed_pct is linear in price for fixed pct."""
    pct = Decimal("0.001")
    assert slippage.fixed_pct(price=Decimal("100"), fixed_slippage_pct=pct) == Decimal("0.100")
    assert slippage.fixed_pct(price=Decimal("200"), fixed_slippage_pct=pct) == Decimal("0.200")


# --- proportional_to_qty -----------------------------------------------------


def test_proportional_to_qty_zero_qty_yields_zero() -> None:
    """Edge: zero qty → zero slippage even with non-zero coeff."""
    assert slippage.proportional_to_qty(
        price=Decimal("65000"),
        qty=Decimal("0"),
        qty_slippage_coeff=Decimal("0.0001"),
    ) == Decimal("0")


def test_proportional_to_qty_scales_with_qty() -> None:
    """Hand verification §A.2: price=65000, qty=0.5, coeff=0.0001 → slippage=3.25."""
    assert slippage.proportional_to_qty(
        price=Decimal("65000"),
        qty=Decimal("0.5"),
        qty_slippage_coeff=Decimal("0.0001"),
    ) == Decimal("3.25000")


def test_proportional_to_qty_negative_qty_uses_abs() -> None:
    """Sell-side qty is negative; slippage uses abs(qty) so always non-negative."""
    assert slippage.proportional_to_qty(
        price=Decimal("65000"),
        qty=Decimal("-0.5"),
        qty_slippage_coeff=Decimal("0.0001"),
    ) == Decimal("3.25000")


# --- half_spread -------------------------------------------------------------


def test_half_spread_zero_range_yields_zero() -> None:
    """high == low → zero range → zero slippage."""
    assert slippage.half_spread(
        high=Decimal("65000"),
        low=Decimal("65000"),
        half_spread_factor=Decimal("1.0"),
    ) == Decimal("0")


def test_half_spread_positive_range_yields_half_with_factor_1() -> None:
    """Hand verification §A.3: high=65100, low=64900, factor=1.0 → slippage=100.0."""
    assert slippage.half_spread(
        high=Decimal("65100"),
        low=Decimal("64900"),
        half_spread_factor=Decimal("1.0"),
    ) == Decimal("100.0")


def test_half_spread_factor_scales_output() -> None:
    """factor=0.5 halves the half-spread; factor=2.0 doubles it."""
    assert slippage.half_spread(
        high=Decimal("65100"),
        low=Decimal("64900"),
        half_spread_factor=Decimal("0.5"),
    ) == Decimal("50.00")
    assert slippage.half_spread(
        high=Decimal("65100"),
        low=Decimal("64900"),
        half_spread_factor=Decimal("2.0"),
    ) == Decimal("200.0")


# --- Hypothesis property test ------------------------------------------------


@given(
    price=st.decimals(
        min_value=Decimal("0.01"),
        max_value=Decimal("1000000"),
        allow_nan=False,
        allow_infinity=False,
        places=8,
    ),
    fixed_slippage_pct=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("0.1"),
        allow_nan=False,
        allow_infinity=False,
        places=8,
    ),
)
def test_fixed_pct_is_non_negative_for_non_negative_inputs(
    price: Decimal,
    fixed_slippage_pct: Decimal,
) -> None:
    """For any non-negative price + pct, slippage is non-negative."""
    assert slippage.fixed_pct(price=price, fixed_slippage_pct=fixed_slippage_pct) >= Decimal("0")


@given(
    high=st.decimals(
        min_value=Decimal("100"),
        max_value=Decimal("1000000"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
    range_pct=st.decimals(
        min_value=Decimal("0.0001"),
        max_value=Decimal("0.1"),
        allow_nan=False,
        allow_infinity=False,
        places=8,
    ),
    factor=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("3"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
)
def test_half_spread_is_non_negative_for_high_geq_low(
    high: Decimal,
    range_pct: Decimal,
    factor: Decimal,
) -> None:
    """For high >= low and non-negative factor, slippage is non-negative."""
    low = high * (Decimal("1") - range_pct)
    if low > high:
        pytest.skip("invalid input: low > high")
    result = slippage.half_spread(high=high, low=low, half_spread_factor=factor)
    assert result >= Decimal("0")
