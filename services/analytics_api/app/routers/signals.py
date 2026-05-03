"""``/api/signals/*`` read endpoints (T-403, BRIEF §9.6:1625 + §14.3:2061).

Two endpoints:

* ``GET /api/signals/`` — paginated + filtered signal feed. Filters:
  source, symbol, action (Action StrEnum-validated → 422 on garbage
  per WG#1), ingestion_status (IngestionStatus StrEnum-validated →
  422), received_at range (`?from=` / `?to=` ISO-8601). Pagination
  via ``?limit=`` (1..200, default 50) + ``?offset=`` (≥0, default 0).
  Response envelope: ``signals`` + ``total`` + ``limit`` + ``offset``.
* ``GET /api/signals/{signal_id}`` — single signal detail; 404 if
  missing. NOTE: hypertable lookup walks every chunk (signals PK is
  composite ``(received_at, id)``, no chunk pruning predicate); MVP-
  scale acceptable per query module docstring.

T-403 ships no `?bot_id=` filter — signals lack bot_id column (signals
are cross-bot per-symbol). No drill-down full timeline backend join;
T-414 / T-418 UI orchestrate via this endpoint + `/api/scoring/by-signal/{id}`.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — FastAPI inspects Query[datetime] at runtime
from typing import Annotated

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, HTTPException, Query, status

from packages.core.types import (  # noqa: TC001 — FastAPI inspects Query[StrEnum] at runtime
    Action,
    IngestionStatus,
)
from packages.db.queries.analytics import (
    count_signals,
    select_signal_by_id,
    select_signals_paginated,
)

from ..deps import get_pool
from ..models.signals import SignalListResponse, SignalResponse

__all__ = ["router"]


router = APIRouter(prefix="/api/signals", tags=["signals"])


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@router.get("/", response_model=SignalListResponse)
async def list_signals(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    source: Annotated[str | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    action: Annotated[Action | None, Query()] = None,
    ingestion_status: Annotated[IngestionStatus | None, Query()] = None,
    from_at: Annotated[
        datetime | None,
        Query(alias="from", description="received_at >= from (ISO-8601)."),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(alias="to", description="received_at < to (ISO-8601)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SignalListResponse:
    """Paginated + filtered signal feed (ORDER BY received_at DESC)."""
    async with pool.acquire() as conn:
        rows = await select_signals_paginated(
            conn,
            source=source,
            symbol=symbol,
            action=action,
            ingestion_status=ingestion_status,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            offset=offset,
        )
        total = await count_signals(
            conn,
            source=source,
            symbol=symbol,
            action=action,
            ingestion_status=ingestion_status,
            from_at=from_at,
            to_at=to_at,
        )
    return SignalListResponse(
        signals=[SignalResponse(**asdict(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{signal_id}", response_model=SignalResponse)
async def get_signal(
    signal_id: int,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> SignalResponse:
    """Return one signal by id; 404 if missing."""
    async with pool.acquire() as conn:
        row = await select_signal_by_id(conn, signal_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"signal {signal_id} not found",
        )
    return SignalResponse(**asdict(row))
