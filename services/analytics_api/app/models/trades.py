"""Pydantic response models for ``/api/trades/*`` (T-402).

Mirror :class:`packages.db.queries.analytics.TradeRow` shape — the 19
fields from the ``trades`` table per BRIEF §7.2:983-1011.

Decimal vs float column split:
* NUMERIC columns (entry_price / exit_price / qty / notional_usd /
  realized_pnl / fees_paid) use :class:`decimal.Decimal` per §N1 /
  §5.3 — precision invariant; Pydantic v2 default serializes as string.
* DOUBLE PRECISION columns (mfe_pct / mae_pct / confidence_score) stay
  as :class:`float` per §5.13 — statistical ratios, not money.

``status`` uses canonical :class:`packages.core.types.TradeStatus`
StrEnum per WG#1 (consistency with T-401a/T-401b precedent).
``meta`` JSONB round-trips as ``dict`` via T-401a's
``_register_jsonb_codec``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from decimal import Decimal  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.core.types import TradeStatus  # noqa: TC001 — Pydantic runtime needs the StrEnum

__all__ = ["TradeListResponse", "TradeResponse"]


class TradeResponse(BaseModel):
    """Single ``trades`` row projected to JSON."""

    model_config = ConfigDict(use_enum_values=True)

    id: int
    bot_id: str
    signal_id: int | None
    open_order_id: int
    close_order_id: int | None
    symbol: str
    side: str
    entry_price: Decimal
    exit_price: Decimal | None
    qty: Decimal
    notional_usd: Decimal
    realized_pnl: Decimal | None
    fees_paid: Decimal | None
    close_reason: str | None
    opened_at: datetime
    closed_at: datetime | None
    status: TradeStatus
    mfe_pct: float | None
    mae_pct: float | None
    confidence_score: float | None
    meta: dict[str, Any]


class TradeListResponse(BaseModel):
    """Paginated envelope: ``trades`` + ``total`` count + ``limit`` + ``offset``."""

    trades: list[TradeResponse]
    total: int
    limit: int
    offset: int
