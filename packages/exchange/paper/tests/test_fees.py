"""§12.1 line 1940 fee computation — T-213a unit tests.

§N4 TDD discipline: tests written FIRST per operator-locked
implementation order. Hand verification §B in docs/plans/T-213.md.
"""

from __future__ import annotations

from decimal import Decimal

from packages.exchange.paper.fees import compute_fee


def test_compute_fee_typical_case() -> None:
    """Hand verification §B: qty=0.5, price=65032.5, rate=0.0006 → fee=19.50975000."""
    assert compute_fee(
        qty=Decimal("0.5"),
        fill_price=Decimal("65032.5"),
        fee_rate=Decimal("0.0006"),
    ) == Decimal("19.50975000")


def test_compute_fee_zero_qty_yields_zero() -> None:
    """Edge: zero qty → zero fee."""
    assert compute_fee(
        qty=Decimal("0"),
        fill_price=Decimal("65000"),
        fee_rate=Decimal("0.0006"),
    ) == Decimal("0")


def test_compute_fee_negative_qty_yields_positive() -> None:
    """Sell-side negative qty produces positive fee per abs."""
    assert compute_fee(
        qty=Decimal("-0.5"),
        fill_price=Decimal("65032.5"),
        fee_rate=Decimal("0.0006"),
    ) == Decimal("19.50975000")


def test_compute_fee_zero_rate_yields_zero() -> None:
    """Edge: zero fee_rate → zero fee."""
    assert compute_fee(
        qty=Decimal("0.5"),
        fill_price=Decimal("65000"),
        fee_rate=Decimal("0"),
    ) == Decimal("0")


def test_compute_fee_buy_sl_fill_d1() -> None:
    """Hand verification §D.1 fee: qty=0.5, fill_price=64500, rate=0.0006 → fee=19.350000."""
    assert compute_fee(
        qty=Decimal("0.5"),
        fill_price=Decimal("64500"),
        fee_rate=Decimal("0.0006"),
    ) == Decimal("19.350000")


def test_compute_fee_buy_tp_fill_d2() -> None:
    """Hand verification §D.2 fee: qty=0.5, fill_price=65500, rate=0.0006 → fee=19.650000."""
    assert compute_fee(
        qty=Decimal("0.5"),
        fill_price=Decimal("65500"),
        fee_rate=Decimal("0.0006"),
    ) == Decimal("19.650000")


def test_compute_fee_sell_partial_d4() -> None:
    """Hand verification §D.4 fee: qty=0.3, fill_price=65500, rate=0.0006 → fee=11.790000."""
    assert compute_fee(
        qty=Decimal("0.3"),
        fill_price=Decimal("65500"),
        fee_rate=Decimal("0.0006"),
    ) == Decimal("11.790000")
