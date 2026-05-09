"""Pydantic response models for ``/api/trades/{id}/shadow-variants`` +
``/api/paper-trades/{id}/shadow-variants`` (T-516b).

15-col mirror of :class:`packages.db.queries.shadow.ShadowVariantRow`
(migration 0015 schema). ``parent_kind`` discriminator preserved per
ADR-0010 — analytics-api is read-only consumer; routing by
``parent_kind`` is a UI/backend convention enforced at the route layer
(``trades.py`` hardcodes ``parent_kind="live"``; ``paper_trades.py``
hardcodes ``parent_kind="paper"``), NOT a query parameter.

Decimal vs float column split (mirror live trades / paper trades models):
* NUMERIC columns (entry_price / qty / realized_pnl) use
  :class:`decimal.Decimal` per §N1 / §5.3 — precision invariant; Pydantic
  v2 default serializes as string.
* DOUBLE PRECISION columns (mfe_pct / mae_pct) stay as :class:`float`
  per §5.13 — statistical ratios, not money.

``terminal_outcome`` uses canonical
:class:`packages.core.types.ShadowVariantTerminal` StrEnum (5 outcomes:
``sl_hit / be_hit / tp_trail / tp_full / timeout``). Reused per L-007
(no redefinition). ``meta`` JSONB round-trips as ``dict`` via
analytics-api lifespan ``_register_jsonb_codec``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from decimal import Decimal  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from packages.core.types import (
    ShadowVariantTerminal,  # noqa: TC001 — Pydantic runtime needs the StrEnum
)

__all__ = ["ShadowVariantListResponse", "ShadowVariantResponse"]


class ShadowVariantResponse(BaseModel):
    """Single ``shadow_variants`` row projected to JSON (15 cols)."""

    model_config = ConfigDict(use_enum_values=True)

    id: int
    parent_trade_id: int
    bot_id: str
    variant_name: str
    side: str
    entry_price: Decimal
    qty: Decimal
    created_at: datetime
    terminated_at: datetime | None
    terminal_outcome: ShadowVariantTerminal | None
    realized_pnl: Decimal | None
    mfe_pct: float | None
    mae_pct: float | None
    meta: dict[str, Any]
    parent_kind: Literal["live", "paper"]


class ShadowVariantListResponse(BaseModel):
    """Envelope: ``variants`` list (no pagination — typically ≤5 per parent)."""

    variants: list[ShadowVariantResponse]
