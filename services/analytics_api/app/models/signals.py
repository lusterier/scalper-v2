"""Pydantic response models for ``/api/signals/*`` (T-403).

Mirror :class:`packages.db.queries.analytics.SignalRow` shape — the 12
fields from the ``signals`` table per BRIEF §7.2:880-893.

``action`` uses canonical :class:`packages.core.types.Action` StrEnum;
``ingestion_status`` uses :class:`packages.core.types.IngestionStatus`
StrEnum (T-403 WG#1 + T-401b/T-402 precedent). FastAPI / Pydantic
serialise StrEnum members to JSON as their string value via
``use_enum_values=True``.

``payload`` JSONB column round-trips as ``dict`` via T-401a's
``_register_jsonb_codec``.

T-403 has NO NUMERIC columns on signals — no Decimal-as-string concern.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any

from pydantic import BaseModel, ConfigDict

from packages.core.types import (  # noqa: TC001 — Pydantic runtime needs the StrEnums
    Action,
    IngestionStatus,
)

__all__ = ["SignalListResponse", "SignalResponse"]


class SignalResponse(BaseModel):
    """Single ``signals`` row projected to JSON."""

    model_config = ConfigDict(use_enum_values=True)

    id: int
    received_at: datetime
    schema_version: str
    source: str
    idempotency_key: str
    symbol: str
    original_symbol: str | None
    action: Action
    payload: dict[str, Any]
    ingestion_status: IngestionStatus
    correlation_id: str


class SignalListResponse(BaseModel):
    """Paginated envelope: ``signals`` + ``total`` count + ``limit`` + ``offset``."""

    signals: list[SignalResponse]
    total: int
    limit: int
    offset: int
