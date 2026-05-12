"""``/api/shadow/rejected/*`` read endpoints (T-517b1, BRIEF §13.6 third bullet).

Per-rejected-signal explorer surface: paginated + filtered list +
single-detail. Read-only consumer of T-513 60-min observation output
written to ``shadow_rejected`` table (BRIEF §13.5).

Two endpoints:

* ``GET /api/shadow/rejected/`` — paginated + filtered list. Filters:
  bot_id, symbol, status (active=``terminated_at IS NULL`` /
  terminated=``IS NOT NULL``), terminal_outcome (ShadowRejectedTerminal
  enum-validated → 422 on garbage), created_at range (`?from=` / `?to=`
  ISO-8601). Pagination via ``?limit=`` (1..200, default 50) +
  ``?offset=`` (≥0, default 0). Response envelope includes ``rejected``
  + ``total`` + ``limit`` + ``offset``.
* ``GET /api/shadow/rejected/{rejected_id}`` — single shadow_rejected
  detail; 404 if missing.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — FastAPI inspects Query[datetime] at runtime
from typing import Annotated, Literal

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, HTTPException, Query, status

from packages.core.types import (
    ShadowRejectedTerminal,  # noqa: TC001 — FastAPI inspects Query[ShadowRejectedTerminal] at runtime
)
from packages.db.queries.shadow import (
    count_shadow_rejected,
    select_shadow_rejected_by_id,
    select_shadow_rejected_paginated,
)

from ..deps import get_pool
from ..models.shadow_rejected import ShadowRejectedListResponse, ShadowRejectedResponse

__all__ = ["router"]


router = APIRouter(prefix="/api/shadow/rejected", tags=["shadow-rejected"])


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@router.get("/", response_model=ShadowRejectedListResponse)
async def list_shadow_rejected(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    bot_id: Annotated[str | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    rejected_status: Annotated[
        Literal["active", "terminated"] | None,
        Query(alias="status", description="Filter by observation status (active / terminated)."),
    ] = None,
    terminal_outcome: Annotated[
        ShadowRejectedTerminal | None,
        Query(
            description=(
                "Filter by terminal outcome "
                "(would_tp / would_sl / would_be / no_trigger / shutdown_mid_replay)."
            ),
        ),
    ] = None,
    from_at: Annotated[
        datetime | None,
        Query(alias="from", description="created_at >= from (ISO-8601)."),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(alias="to", description="created_at < to (ISO-8601)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ShadowRejectedListResponse:
    """Paginated + filtered shadow_rejected list (ORDER BY created_at DESC, id DESC)."""
    async with pool.acquire() as conn:
        rows = await select_shadow_rejected_paginated(
            conn,
            bot_id=bot_id,
            symbol=symbol,
            status=rejected_status,
            terminal_outcome=terminal_outcome,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            offset=offset,
        )
        total = await count_shadow_rejected(
            conn,
            bot_id=bot_id,
            symbol=symbol,
            status=rejected_status,
            terminal_outcome=terminal_outcome,
            from_at=from_at,
            to_at=to_at,
        )
    return ShadowRejectedListResponse(
        rejected=[ShadowRejectedResponse(**asdict(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{rejected_id}", response_model=ShadowRejectedResponse)
async def get_shadow_rejected(
    rejected_id: int,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> ShadowRejectedResponse:
    """Return one shadow_rejected by PK; 404 if missing."""
    async with pool.acquire() as conn:
        row = await select_shadow_rejected_by_id(conn, rejected_id=rejected_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"shadow_rejected {rejected_id} not found",
        )
    return ShadowRejectedResponse(**asdict(row))
