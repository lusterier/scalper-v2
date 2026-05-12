"""Pydantic response models for ``/api/shadow/rejected/*`` (T-517b1).

11-col mirror of :class:`packages.db.queries.shadow.ShadowRejectedRow`
(migration 0014 schema). Read-only consumer of T-513 60-min observation
output (BRIEF §13.5). Mirror
:class:`services.analytics_api.app.models.paper_trades.PaperTradeListResponse`
envelope shape for paginated list + single-detail responses.

Decimal vs float column split:
* No NUMERIC columns — rejected signals have no realized P&L (they did not
  trade per BRIEF §13.5).
* DOUBLE PRECISION columns (mfe_pct / mae_pct) stay as :class:`float` per
  §5.13 — statistical ratios.

``terminal_outcome`` uses canonical
:class:`packages.core.types.ShadowRejectedTerminal` StrEnum (5 outcomes:
``would_tp / would_sl / would_be / no_trigger / shutdown_mid_replay``).
``meta`` JSONB round-trips as ``dict`` via analytics-api lifespan
``_register_jsonb_codec`` (per L-011).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.core.types import (
    ShadowRejectedTerminal,  # noqa: TC001 — Pydantic runtime needs the StrEnum
)

__all__ = ["ShadowRejectedListResponse", "ShadowRejectedResponse"]


class ShadowRejectedResponse(BaseModel):
    """Single ``shadow_rejected`` row projected to JSON (11 cols)."""

    model_config = ConfigDict(use_enum_values=True)

    id: int
    signal_id: int
    bot_id: str
    symbol: str
    would_side: str
    created_at: datetime
    terminated_at: datetime | None
    terminal_outcome: ShadowRejectedTerminal | None
    mfe_pct: float | None
    mae_pct: float | None
    meta: dict[str, Any]


class ShadowRejectedListResponse(BaseModel):
    """Paginated envelope: ``rejected`` + ``total`` count + ``limit`` + ``offset``."""

    rejected: list[ShadowRejectedResponse]
    total: int
    limit: int
    offset: int
