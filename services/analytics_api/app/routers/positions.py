"""``/api/positions/*`` read endpoints (T-402, BRIEF §9.6:1623 + §14.3:2061).

One endpoint:

* ``GET /api/positions/`` — list all open positions across bots, with
  optional ``?bot_id=`` filter. No pagination (positions <50 across
  active bots per §14.3:2061 dashboard expectations).

T-402 ships read-only — admin write endpoints (pause/resume) live in
T-420 / separate task per §0.8 anti-hypothetical + T-401 OQ-2 default.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, Query

from packages.db.queries.analytics import select_open_positions

from ..deps import get_pool
from ..models.positions import OpenPositionListResponse, OpenPositionResponse

__all__ = ["router"]


router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("/", response_model=OpenPositionListResponse)
async def list_open_positions(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    bot_id: Annotated[
        str | None,
        Query(description="Filter to one bot's positions; omit for cross-bot view."),
    ] = None,
) -> OpenPositionListResponse:
    """Return all open positions across bots, or filter to one bot."""
    async with pool.acquire() as conn:
        rows = await select_open_positions(conn, bot_id=bot_id)
    return OpenPositionListResponse(
        positions=[OpenPositionResponse(**asdict(r)) for r in rows],
    )
