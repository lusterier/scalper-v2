"""``/api/trades/*`` read endpoints (T-402, BRIEF §9.6:1624 + §14.3:2062).

Two endpoints:

* ``GET /api/trades/`` — paginated + filtered list. Filters: bot_id,
  symbol, status (TradeStatus enum-validated → 422 on garbage per WG#1),
  closed_at range (`?from=` / `?to=` ISO-8601). Pagination via
  ``?limit=`` (1..200, default 50) + ``?offset=`` (≥0, default 0).
  Response envelope includes ``trades`` + ``total`` + ``limit`` +
  ``offset``.
* ``GET /api/trades/{trade_id}`` — single trade detail; 404 if missing.

Drill-down full timeline (signal + scoring + executions + orders +
shadow + post-close snapshots per BRIEF §14.3:2062) is **T-414 UI
orchestration concern**, NOT backend join — UI joins via T-402 +
T-403 + T-404 + T-407 endpoint groups client-side per §0.8
anti-hypothetical + OQ-3 default.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — FastAPI inspects Query[datetime] at runtime
from typing import Annotated

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, HTTPException, Query, status

from packages.core.types import (
    TradeStatus,  # noqa: TC001 — FastAPI inspects Query[TradeStatus] at runtime
)
from packages.db.queries.analytics import (
    count_trades,
    select_trade_by_id,
    select_trades_paginated,
)

from ..deps import get_pool
from ..models.trades import TradeListResponse, TradeResponse

__all__ = ["router"]


router = APIRouter(prefix="/api/trades", tags=["trades"])


_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@router.get("/", response_model=TradeListResponse)
async def list_trades(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    bot_id: Annotated[str | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    trade_status: Annotated[
        TradeStatus | None,
        Query(alias="status", description="Filter by trade status (open / closed / error)."),
    ] = None,
    from_at: Annotated[
        datetime | None,
        Query(alias="from", description="closed_at >= from (ISO-8601)."),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(alias="to", description="closed_at < to (ISO-8601)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TradeListResponse:
    """Paginated + filtered trade list (ORDER BY closed_at DESC NULLS FIRST)."""
    async with pool.acquire() as conn:
        rows = await select_trades_paginated(
            conn,
            bot_id=bot_id,
            symbol=symbol,
            status=trade_status,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            offset=offset,
        )
        total = await count_trades(
            conn,
            bot_id=bot_id,
            symbol=symbol,
            status=trade_status,
            from_at=from_at,
            to_at=to_at,
        )
    return TradeListResponse(
        trades=[TradeResponse(**asdict(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{trade_id}", response_model=TradeResponse)
async def get_trade(
    trade_id: int,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> TradeResponse:
    """Return one trade by PK; 404 if missing."""
    async with pool.acquire() as conn:
        row = await select_trade_by_id(conn, trade_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"trade {trade_id} not found",
        )
    return TradeResponse(**asdict(row))
