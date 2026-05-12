"""Pydantic response models for ``/api/shadow/aggregate/{symbol}`` (T-517a1).

Per-variant aggregated metrics surface (BRIEF §13.6 second bullet "which
variant would have been best over last N trades?"). Mirror
:class:`services.analytics_api.app.models.shadow_rejected.ShadowRejectedListResponse`
envelope shape modulo aggregate-specific filter echo.

Decimal vs float column split:

* NUMERIC columns (total_pnl / avg_pnl / best_pnl / worst_pnl) use
  :class:`decimal.Decimal` per §N1 / §5.3 — money sums; Pydantic v2 default
  serializes as string.
* DOUBLE PRECISION (win_rate / avg_mfe_pct / avg_mae_pct) stay as
  :class:`float` per §5.13 — statistical ratios.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from decimal import Decimal  # noqa: TC003 — Pydantic runtime needs the type

from pydantic import BaseModel

__all__ = ["VariantAggregateListResponse", "VariantAggregateResponse"]


class VariantAggregateResponse(BaseModel):
    """Single variant's aggregated metrics (8 metrics + variant_name + n_trades)."""

    variant_name: str
    n_trades: int
    win_count: int
    win_rate: float
    total_pnl: Decimal
    avg_pnl: Decimal
    best_pnl: Decimal
    worst_pnl: Decimal
    avg_mfe_pct: float | None
    avg_mae_pct: float | None


class VariantAggregateListResponse(BaseModel):
    """Envelope: ``variants`` (sorted by total_pnl DESC tiebreak variant_name ASC)
    + ``symbol`` + filter echo.
    """

    symbol: str
    variants: list[VariantAggregateResponse]
    bot_id: str | None
    from_at: datetime | None
    to_at: datetime | None
