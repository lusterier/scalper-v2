"""§12.1 line 1940 fee computation.

Hand verification §B in docs/plans/T-213.md provides hand-computed
values; tests assert exact Decimal equality.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

__all__ = ["compute_fee"]


def compute_fee(
    *,
    qty: Decimal,
    fill_price: Decimal,
    fee_rate: Decimal,
) -> Decimal:
    """``fee = abs(qty) * fill_price * fee_rate``. Always positive.

    §12.1 line 1940: "fees deducted at fill time, same as live."
    Stored as NUMERIC(20, 8) per paper_executions schema (T-212).

    Hand verification §B: qty=0.5, price=65032.5, rate=0.0006 →
    fee = 0.5 * 65032.5 * 0.0006 = 19.50975.
    """
    return abs(qty) * fill_price * fee_rate
