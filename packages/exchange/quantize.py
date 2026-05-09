"""Pre-flight qty quantization helper (T-529 / H-036).

Caller-agnostic; consumes :class:`InstrumentInfo` + raw qty. Returns rounded
qty if all constraints satisfied; raises :class:`QtyValidationError` otherwise.
Decimal arithmetic throughout per §5.3 (no float casts).

Round-down semantic per OQ-1 + §5.3: ``qty // qty_step * qty_step`` (NOT
round-half-up). Under-shoot is conservative — avoids accidentally exceeding
``bot_config.execution.qty`` budget.

minNotional pre-flight DEFERRED to T-529-future (requires last_price; out of
T-529 narrow scope). Bybit-side handles minNotional violations via existing
:class:`OrderRejected` handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from packages.exchange.errors import QtyValidationError

if TYPE_CHECKING:
    from decimal import Decimal

    from packages.exchange.types import InstrumentInfo

__all__ = ["quantize_qty"]


def quantize_qty(qty: Decimal, info: InstrumentInfo) -> Decimal:
    """Round qty DOWN to ``info.qty_step``; validate ``>= info.min_order_qty``.

    Hand-verified cases (math-validator Gate 4 fixture):

    - Aligned: ``Decimal("0.001") // Decimal("0.001") * Decimal("0.001")``
      → ``Decimal("0.001")`` (unchanged).
    - Round-down: ``Decimal("0.0015") // Decimal("0.001") * Decimal("0.001")``
      → ``Decimal("1") * Decimal("0.001")`` → ``Decimal("0.001")``.
    - Below floor pre-round: ``qty=0.0005, min=0.001`` →
      :class:`QtyValidationError("min_order_qty")`.
    - Below floor post-round: ``qty=0.0009, step=0.001, min=0.001`` →
      ``qty // step = 0`` → ``0 * step = 0`` → :class:`QtyValidationError("min_order_qty")`.

    Decimal ``//`` is Python floor-div per spec (rounds toward -∞); preserves
    precision (no binary-float roundoff per §5.3).
    """
    if qty < info.min_order_qty:
        raise QtyValidationError(info.symbol, "min_order_qty", qty, info)
    rounded = (qty // info.qty_step) * info.qty_step
    if rounded < info.min_order_qty:
        raise QtyValidationError(info.symbol, "min_order_qty", qty, info)
    return rounded
