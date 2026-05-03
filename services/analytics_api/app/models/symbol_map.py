"""Pydantic request + response models for ``/api/symbol-map/*`` (T-401b).

Mirror :class:`packages.db.queries.analytics.SymbolMapRow` shape — the
6 columns from the ``symbol_map`` table per BRIEF §7.2:1131-1138.
``exchange_source`` uses the canonical
:class:`packages.core.types.ExchangeSource` StrEnum (T-401b WG#1
consistency with T-401a's :class:`BotStatus` / :class:`ExchangeMode`
StrEnum precedent); FastAPI / Pydantic serialise StrEnum members to
JSON as their string value via ``use_enum_values=True``.

POST creates a new entry; PUT overwrites all mutable fields (full PUT
semantics; ``notes=null`` is explicit clear, NOT PATCH-style "skip
field" — per §0.8 anti-hypothetical OQ-8 default 2026-05-03).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type

from pydantic import BaseModel, ConfigDict, Field

from packages.core.types import ExchangeSource  # noqa: TC001 — Pydantic runtime needs the StrEnum

__all__ = [
    "SymbolMapEntryCreate",
    "SymbolMapEntryResponse",
    "SymbolMapEntryUpdate",
    "SymbolMapListResponse",
]


class SymbolMapEntryResponse(BaseModel):
    """Single ``symbol_map`` row projected to JSON for the dashboard."""

    model_config = ConfigDict(use_enum_values=True)

    input_symbol: str
    canonical_symbol: str
    exchange_source: ExchangeSource
    notes: str | None
    created_at: datetime
    updated_at: datetime


class SymbolMapEntryCreate(BaseModel):
    """POST /api/symbol-map/ request body."""

    model_config = ConfigDict(use_enum_values=True)

    input_symbol: str = Field(..., min_length=1, max_length=64)
    canonical_symbol: str = Field(..., min_length=1, max_length=64)
    exchange_source: ExchangeSource
    notes: str | None = Field(None, max_length=500)


class SymbolMapEntryUpdate(BaseModel):
    """PUT /api/symbol-map/{input_symbol} request body.

    Full PUT semantics: all mutable fields required. ``input_symbol``
    is in the URL path, NOT the body (per WG#10 — entity_id derives
    from path parameter).
    """

    model_config = ConfigDict(use_enum_values=True)

    canonical_symbol: str = Field(..., min_length=1, max_length=64)
    exchange_source: ExchangeSource
    notes: str | None = Field(None, max_length=500)


class SymbolMapListResponse(BaseModel):
    """Envelope for ``GET /api/symbol-map/`` collection response."""

    entries: list[SymbolMapEntryResponse]
