"""§N4 unit tests for :mod:`services.execution.app.reconcile` (T-218b stub-pin).

T-218b ships :func:`reconcile_close` as a stub raising ``NotImplementedError``;
T-219 will replace the body with cumulative-delta P&L close flow per §9.5:1594-1599.
These tests pin the stub's substring contract and signature surface so the
T-218b → T-219 forward-pointer is verified at gate-3 brief-review.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from services.execution.app.reconcile import reconcile_close

_FIXED_NOW = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)


async def test_reconcile_close_raises_not_implemented_with_T_219_substring() -> None:
    """T-219 stub-pin (mirror T-216a→T-216b NotImplementedError substring pattern).

    Error message MUST contain 'T-219' substring so the forward-pointer is
    grep-able and brief-reviewer can verify at SHIP gate.
    """
    with pytest.raises(NotImplementedError, match="T-219"):
        await reconcile_close(
            conn=MagicMock(),
            bound_logger=MagicMock(),
            bot_id="alpha",
            symbol="BTCUSDT",
            trade_id=1,
            close_order_id=100,
            final_fill_price=Decimal("100"),
            final_fill_qty=Decimal("10"),
            final_fill_fee=Decimal("0.05"),
            closed_at=_FIXED_NOW,
        )


async def test_reconcile_close_signature_accepts_required_kwargs() -> None:
    """Surface contract pin — caller in dispatcher.py invokes with these exact kwargs.

    Verifies (via successful-call signature compatibility — raises
    NotImplementedError but reaches the body without TypeError) that the
    stub accepts the kwarg surface T-218b dispatcher uses.
    """
    with pytest.raises(NotImplementedError):
        # If signature drifts, this raises TypeError before NotImplementedError.
        await reconcile_close(
            conn=MagicMock(),
            bound_logger=MagicMock(),
            bot_id="alpha",
            symbol="BTCUSDT",
            trade_id=1,
            close_order_id=None,  # nullable per signature
            final_fill_price=Decimal("100"),
            final_fill_qty=Decimal("10"),
            final_fill_fee=Decimal("0.05"),
            closed_at=_FIXED_NOW,
        )
