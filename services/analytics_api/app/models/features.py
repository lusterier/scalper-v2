"""Pydantic response models for ``/api/features/*`` (T-404).

Mirror :class:`packages.db.queries.analytics.FeatureRow` shape — the
7 fields from the ``features`` table per BRIEF §7.2:903-911.

DOUBLE PRECISION ``value_num`` → JSON number (float) per §5.13 —
statistical metrics, not money. ``value_json`` JSONB column round-trips
as ``dict`` OR ``list`` via T-401a's ``_register_jsonb_codec``.

Per T-404 OQ-5 default A — keep 3 separate fields (value_num,
value_bool, value_json) faithful to schema. UI handles polymorphism
per feature definition. ``value_json`` Pydantic union accepts JSON
object ``{}`` or array ``[]`` — Pydantic v2 default mode preserves
shape (dict stays dict, list stays list, no coercion).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any

from pydantic import BaseModel

__all__ = [
    "FeatureHistoryListResponse",
    "FeatureLatestListResponse",
    "FeatureResponse",
]


class FeatureResponse(BaseModel):
    """Single ``features`` row projected to JSON."""

    feature_name: str
    symbol: str
    computed_at: datetime
    value_num: float | None
    value_bool: bool | None
    value_json: dict[str, Any] | list[Any] | None
    source_version: str


class FeatureLatestListResponse(BaseModel):
    """Paginated envelope for ``GET /api/features/latest``."""

    features: list[FeatureResponse]
    total: int
    limit: int
    offset: int


class FeatureHistoryListResponse(BaseModel):
    """Paginated envelope for ``GET /api/features/history``."""

    features: list[FeatureResponse]
    total: int
    limit: int
    offset: int
