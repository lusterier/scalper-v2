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

from decimal import ROUND_CEILING, ROUND_FLOOR
from typing import TYPE_CHECKING

from packages.exchange.errors import QtyValidationError

if TYPE_CHECKING:
    from decimal import Decimal
    from typing import Literal

    from packages.exchange.types import InstrumentInfo

__all__ = ["quantize_price", "quantize_qty"]


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


def quantize_price(price: Decimal, side: Literal["buy", "sell"], tick: Decimal) -> Decimal:
    """Align ``price`` to the instrument ``tick`` grid, side-aware conservative.

    T-558a / finding #2. ``buy`` → ROUND_FLOOR, ``sell`` → ROUND_CEILING.
    For a LONG (buy): SL below entry rounds DOWN (never tighter than
    configured), TP above entry rounds DOWN (never further). For a SHORT
    (sell): SL above entry rounds UP (never tighter), TP below entry rounds
    UP (never further). The rounding direction is purely side-determined and
    is the same for SL, TP, and (T-558b) BE/trail SL.

    Decimal-only §5.3: ``price / tick`` is correctly-rounded to the Decimal
    context; ``.to_integral_value(rounding=...)`` applies the EXPLICIT
    rounding mode (NOT ``//`` — Python ``Decimal //`` truncates toward zero,
    NOT floor toward -∞, so it is wrong for the ceil/negative path; L-037);
    ``* tick`` returns to the grid. No float.

    Hand-verified (math-validator Gate-4 fixture):

    - buy 76017.546, tick 0.1 → ``760175.46`` FLOOR ``760175`` → ``76017.5``.
    - buy 77937.181, tick 0.1 → ``779371`` → ``77937.1``.
    - sell 76017.546, tick 0.1 → ``760175.46`` CEIL ``760176`` → ``76017.6``.
    - on-grid 76017.5, tick 0.1 → unchanged (buy & sell).
    - tick 0.01: buy 100.007 → ``100.00``; sell 100.007 → ``100.01``.
    - tick 0.5 (non-10^-n): buy 100.3 → ``100.0``; sell 100.3 → ``100.5``.
    """
    if tick <= 0:
        raise ValueError(f"tick must be positive, got {tick}")
    rounding = ROUND_FLOOR if side == "buy" else ROUND_CEILING
    return (price / tick).to_integral_value(rounding=rounding) * tick
