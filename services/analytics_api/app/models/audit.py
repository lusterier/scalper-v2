"""Pydantic response models for ``/api/audit/*`` (T-405).

Mirror :class:`packages.db.queries.audit.AuditEventRow` shape — the 10
fields from the ``audit_events`` table per BRIEF §7.2:1108-1126.

JSONB ``before_state`` / ``after_state`` / ``meta`` round-trip as
``dict | None`` via T-401a's ``_register_jsonb_codec``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic runtime needs the type
from typing import Any

from pydantic import BaseModel

__all__ = ["AuditEventListResponse", "AuditEventResponse"]


class AuditEventResponse(BaseModel):
    """Single ``audit_events`` row projected to JSON."""

    id: int
    occurred_at: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    correlation_id: str | None
    meta: dict[str, Any]


class AuditEventListResponse(BaseModel):
    """Paginated envelope for ``GET /api/audit/``."""

    events: list[AuditEventResponse]
    total: int
    limit: int
    offset: int
