"""Pydantic response models for ``/api/bots/*`` (T-401a).

Mirror :class:`packages.db.queries.analytics.BotDetailRow` shape — the
8 columns from the ``bots`` table per BRIEF §7.2:846-859. ``status``
and ``exchange_mode`` use the canonical :class:`packages.core.types`
StrEnums; FastAPI / Pydantic serialise StrEnum members to JSON as
their string value automatically.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.core.types import (  # noqa: TC001 — Pydantic runtime needs the StrEnums
    BotStatus,
    ExchangeMode,
)

__all__ = ["BotListResponse", "BotResponse"]


class BotResponse(BaseModel):
    """Single ``bots`` row projected to JSON for the dashboard."""

    model_config = ConfigDict(use_enum_values=True)

    bot_id: str
    display_name: str
    created_at: datetime
    status: BotStatus
    exchange_mode: ExchangeMode
    config_hash: str
    config_applied_at: datetime
    meta: dict[str, Any]


class BotListResponse(BaseModel):
    """Envelope for ``GET /api/bots/`` collection response."""

    bots: list[BotResponse]
