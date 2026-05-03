"""Pydantic response models for ``/api/positions/*`` (T-402).

Mirror :class:`packages.db.queries.analytics.OpenPositionRow` shape —
the 16 fields from the ``position_state`` table per BRIEF §7.2:1058-1080.

NUMERIC columns (entry_price / qty / sl_price / running_pnl / etc) use
:class:`decimal.Decimal` per §N1 / §5.3 precision invariant. Pydantic
v2 default JSON serialization for Decimal is **string** (not float) —
preserves precision per §N1; UI parses via ``Number(value_str)`` only
where ratios are needed for chart axes.

T-402 ships read-only — no admin write endpoints in this scope.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from decimal import Decimal  # noqa: TC003 — Pydantic runtime needs the type

from pydantic import BaseModel

__all__ = ["OpenPositionListResponse", "OpenPositionResponse"]


class OpenPositionResponse(BaseModel):
    """Single ``position_state`` row projected to JSON for the dashboard."""

    bot_id: str
    symbol: str
    trade_id: int
    side: str
    entry_price: Decimal
    qty: Decimal
    remaining_qty: Decimal
    sl_price: Decimal | None
    tp_price: Decimal | None
    sl_type: str | None
    best_price: Decimal | None
    tp_hit: bool
    trailing_active: bool
    running_pnl: Decimal
    mfe_price: Decimal | None
    mae_price: Decimal | None
    updated_at: datetime


class OpenPositionListResponse(BaseModel):
    """Envelope for ``GET /api/positions/`` collection response."""

    positions: list[OpenPositionResponse]
