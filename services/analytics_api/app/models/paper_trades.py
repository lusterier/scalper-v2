"""Pydantic response models for ``/api/paper-trades/*`` (T-516a1).

Mirror :class:`services.analytics_api.app.models.trades.TradeResponse` 1:1
modulo class name + envelope key. paper_trades schema (migration 0008) is
structurally identical to trades schema (migration 0005) — same 21 fields
+ types + nullability + semantic per §3.1:268 paper-live symmetry invariant.

Decimal vs float column split (mirror live):
* NUMERIC columns (entry_price / exit_price / qty / notional_usd /
  realized_pnl / fees_paid) use :class:`decimal.Decimal` per §N1 /
  §5.3 — precision invariant; Pydantic v2 default serializes as string.
* DOUBLE PRECISION columns (mfe_pct / mae_pct / confidence_score) stay
  as :class:`float` per §5.13 — statistical ratios, not money.

``status`` uses canonical :class:`packages.core.types.TradeStatus` StrEnum
(reuse mirror live; paper_trades.status values are 'open'/'closed' same
as live per OQ-5=A baked operator session 2026-05-08). ``meta`` JSONB
round-trips as ``dict`` via analytics-api lifespan ``_register_jsonb_codec``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from decimal import Decimal  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.core.types import TradeStatus  # noqa: TC001 — Pydantic runtime needs the StrEnum

__all__ = ["PaperTradeListResponse", "PaperTradeResponse"]


class PaperTradeResponse(BaseModel):
    """Single ``paper_trades`` row projected to JSON."""

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


class PaperTradeListResponse(BaseModel):
    """Paginated envelope: ``paper_trades`` + ``total`` count + ``limit`` + ``offset``."""

    paper_trades: list[PaperTradeResponse]
    total: int
    limit: int
    offset: int
