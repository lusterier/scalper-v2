"""§12.1 line 1937 slippage models — pure-functional.

Each function computes ``slippage_amount`` for a fill given the
current candle context + per-bot config. Sign convention applied at
caller site (buy: add to mid_price; sell: subtract).

Hand verification §A in docs/plans/T-213.md provides hand-computed
values for every function; tests assert exact Decimal equality
against those values.

# §N9-exempt mathematical constant: half_spread = range / 2 by
# definition (mathematically fixed, not a configurable business
# number; per L-001 active control). The literal Decimal("2") in
# half_spread is the divisor for "half".
"""

from __future__ import annotations

from decimal import Decimal

__all__ = ["fixed_pct", "half_spread", "proportional_to_qty"]


def fixed_pct(*, price: Decimal, fixed_slippage_pct: Decimal) -> Decimal:
    """``slippage = price * fixed_slippage_pct``. Constant pct of price.

    Hand verification §A.1: price=65000, pct=0.0005 → slippage=32.5000.
    """
    return price * fixed_slippage_pct


def proportional_to_qty(
    *,
    price: Decimal,
    qty: Decimal,
    qty_slippage_coeff: Decimal,
) -> Decimal:
    """``slippage = price * qty_slippage_coeff * abs(qty)``.

    Models market impact: larger orders pay more slippage.
    Hand verification §A.2: price=65000, qty=0.5, coeff=0.0001 →
    slippage = 65000 * 0.0001 * 0.5 = 3.25000.
    """
    return price * qty_slippage_coeff * abs(qty)


def half_spread(
    *,
    high: Decimal,
    low: Decimal,
    half_spread_factor: Decimal,
) -> Decimal:
    """``slippage = (high - low) / 2 * half_spread_factor``.

    Uses candle's high-low range as spread proxy in F2 (F5 backtest
    harness §12.2 uses real bid/ask spread). ``half_spread_factor`` is
    per-bot multiplier (typically 1.0 for full half-spread; 0.5 for
    inside half-spread; etc.).

    Hand verification §A.3: high=65100, low=64900, factor=1.0 →
    slippage = (65100 - 64900) / 2 * 1.0 = 100.0.
    """
    return (high - low) / Decimal("2") * half_spread_factor
