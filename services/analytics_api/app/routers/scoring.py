"""``/api/scoring/*`` read endpoints (T-403, BRIEF §9.6:1626 + §14.3:2066).

One endpoint:

* ``GET /api/scoring/by-signal/{signal_id}`` — list of scoring
  evaluations for one signal across all bots that received it.
  Returns ``200`` with empty ``evaluations: []`` when no evaluations
  exist (signal may have been received before any bot was running, or
  rejected at ingestion before scoring fired) — distinct from
  ``/api/signals/{id}`` 404 entity-not-found semantic.

Per §0.8 anti-hypothetical: NO `/api/scoring/` global paginated list,
NO `/api/scoring/{evaluation_id}` single-evaluation detail (no UI
consumer for either; add per opportunistic demand).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends

from packages.db.queries.analytics import select_scoring_evaluations_by_signal_id

from ..deps import get_pool
from ..models.scoring import (
    ScoringEvaluationListResponse,
    ScoringEvaluationResponse,
)

__all__ = ["router"]


router = APIRouter(prefix="/api/scoring", tags=["scoring"])


@router.get(
    "/by-signal/{signal_id}",
    response_model=ScoringEvaluationListResponse,
)
async def list_evaluations_by_signal(
    signal_id: int,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
) -> ScoringEvaluationListResponse:
    """Return all scoring evaluations for one signal_id (one per bot).

    200 with empty list when no evaluations found — NOT 404 (collection-
    shape endpoint; see module docstring rationale).
    """
    async with pool.acquire() as conn:
        rows = await select_scoring_evaluations_by_signal_id(conn, signal_id)
    return ScoringEvaluationListResponse(
        evaluations=[ScoringEvaluationResponse(**asdict(r)) for r in rows],
    )
