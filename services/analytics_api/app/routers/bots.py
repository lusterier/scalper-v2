"""``/api/bots/*`` read endpoints (T-401a, BRIEF §9.6:1622).

Two endpoints:

* ``GET /api/bots/`` — list all bots (active + paused + archived),
  ordered by ``bot_id`` ASC. No pagination — bots collection is
  bounded at <10 rows for the foreseeable future.
* ``GET /api/bots/{bot_id}`` — single bot detail; 404 if missing.

No write surface in T-401a; bot pause/resume admin endpoints land in
T-420 / separate task per §0.8 anti-hypothetical. No audit row writes
in T-401a (analytics-api skeleton has no other consumers of
:func:`packages.db.queries.audit.insert_audit_event` until T-401b
symbol-map CRUD).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, HTTPException, status

from packages.db.queries.analytics import select_all_bots, select_bot_by_id

from ..deps import get_pool
from ..models.bots import BotListResponse, BotResponse

__all__ = ["router"]


router = APIRouter(prefix="/api/bots", tags=["bots"])


@router.get("/", response_model=BotListResponse)
async def list_bots(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> BotListResponse:
    """Return all bots (active + paused + archived) ordered by bot_id."""
    async with pool.acquire() as conn:
        rows = await select_all_bots(conn)
    return BotListResponse(bots=[BotResponse(**asdict(r)) for r in rows])


@router.get("/{bot_id}", response_model=BotResponse)
async def get_bot(
    bot_id: str,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> BotResponse:
    """Return one bot row; 404 if no row matches ``bot_id``."""
    async with pool.acquire() as conn:
        row = await select_bot_by_id(conn, bot_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"bot {bot_id!r} not found",
        )
    return BotResponse(**asdict(row))
