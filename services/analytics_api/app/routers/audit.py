"""``/api/audit/*`` read endpoints (T-405, BRIEF §9.6:1631 + §14.3:2067).

Two endpoints:

* ``GET /api/audit/`` — paginated + filtered audit_events list. Filters:
  entity_type, entity_id, actor, action_prefix, occurred_at range
  (``?from=`` inclusive / ``?to=`` exclusive). Pagination via ``?limit=``
  (1..200, default 50) + ``?offset=`` (≥0). ORDER BY ``occurred_at DESC``.
* ``GET /api/audit/{event_id}?occurred_at=...`` — composite-PK detail
  lookup; ``?occurred_at=`` query param REQUIRED for hypertable chunk
  pruning per WG#5 (audit_events 30-day-chunk hypertable; UI always has
  occurred_at from list response → no UX cost; F5+ retention growth makes
  chunk-pruning structurally correct). Distinct from T-403 select_signal_by_id
  walks-every-chunk pattern.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — FastAPI inspects Query[datetime] at runtime
from typing import Annotated

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, HTTPException, Query, status

from packages.db.queries.analytics import (
    count_audit_events,
    select_audit_event_by_id,
    select_audit_events_paginated,
)

from ..deps import get_pool
from ..models.audit import AuditEventListResponse, AuditEventResponse

__all__ = ["router"]


router = APIRouter(prefix="/api/audit", tags=["audit"])


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@router.get("/", response_model=AuditEventListResponse)
async def list_audit_events(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
    action_prefix: Annotated[
        str | None,
        Query(
            description=(
                "LIKE-prefix on action column (e.g. 'bot_config.' matches all bot_config.*)."
            ),
        ),
    ] = None,
    from_at: Annotated[
        datetime | None,
        Query(alias="from", description="occurred_at >= from (ISO-8601, inclusive)."),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(alias="to", description="occurred_at < to (ISO-8601, exclusive)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventListResponse:
    """Paginated + filtered audit_events list (ORDER BY occurred_at DESC)."""
    async with pool.acquire() as conn:
        rows = await select_audit_events_paginated(
            conn,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            action_prefix=action_prefix,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            offset=offset,
        )
        total = await count_audit_events(
            conn,
            entity_type=entity_type,
            entity_id=entity_id,
            actor=actor,
            action_prefix=action_prefix,
            from_at=from_at,
            to_at=to_at,
        )
    return AuditEventListResponse(
        events=[AuditEventResponse(**asdict(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{event_id}", response_model=AuditEventResponse)
async def get_audit_event(
    event_id: int,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    occurred_at: Annotated[
        datetime,
        Query(description="Composite PK component for hypertable chunk pruning per WG#5."),
    ],
) -> AuditEventResponse:
    """Return one audit_event by composite PK ``(occurred_at, id)``; 404 if missing."""
    async with pool.acquire() as conn:
        row = await select_audit_event_by_id(
            conn,
            occurred_at=occurred_at,
            event_id=event_id,
        )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"audit event {event_id} at {occurred_at.isoformat()} not found",
        )
    return AuditEventResponse(**asdict(row))
