"""``/api/shadow/aggregate/{symbol}`` per-variant aggregate endpoint (T-517a1).

Per-symbol best-variant aggregate surface (BRIEF §13.6 second bullet "which
variant would have been best over last N trades?"). Read-only consumer of
T-511 + T-512 shadow runtime output (terminated ``shadow_variants`` rows
with finalized ``realized_pnl``).

Single endpoint:

* ``GET /api/shadow/aggregate/{symbol}`` — fetches terminated variants for
  the given symbol via JOIN on parent ``trades`` / ``paper_trades``, then
  aggregates per ``variant_name`` (8 metrics: n_trades + win_count +
  win_rate + total_pnl + avg_pnl + best_pnl + worst_pnl + avg_mfe_pct +
  avg_mae_pct). Optional filters: bot_id, created_at range
  (``?from=`` / ``?to=`` ISO-8601). Response envelope: variants list
  (sorted by total_pnl DESC + variant_name ASC tiebreak) + symbol + filter
  echo. Empty result → 200 with ``variants=[]`` (NOT 404).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — FastAPI inspects Query[datetime] at runtime
from typing import Annotated

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, Query

from packages.db.queries.shadow import select_shadow_variants_for_aggregate

from ..analytics_compute import compute_variant_aggregate
from ..deps import get_pool
from ..models.shadow_aggregate import (
    VariantAggregateListResponse,
    VariantAggregateResponse,
)

__all__ = ["router"]


router = APIRouter(prefix="/api/shadow/aggregate", tags=["shadow-aggregate"])


@router.get("/{symbol}", response_model=VariantAggregateListResponse)
async def get_variant_aggregate(
    symbol: str,
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    bot_id: Annotated[str | None, Query()] = None,
    from_at: Annotated[
        datetime | None,
        Query(alias="from", description="created_at >= from (ISO-8601)."),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(alias="to", description="created_at < to (ISO-8601)."),
    ] = None,
) -> VariantAggregateListResponse:
    """Aggregate terminated shadow variants for ``symbol`` by variant_name.

    Fetch-then-compute pattern (mirror ``/api/analytics/expectancy``):
    SELECT all matching terminated variants → in-memory GROUP BY
    variant_name + 8-metric computation → Pydantic-narrow → return envelope.
    Empty result → 200 with ``variants=[]`` (NOT 404; consistent with
    paper-trades / shadow.rejected empty-result convention).
    """
    async with pool.acquire() as conn:
        rows = await select_shadow_variants_for_aggregate(
            conn,
            symbol=symbol,
            bot_id=bot_id,
            from_at=from_at,
            to_at=to_at,
        )
    metrics = compute_variant_aggregate(rows)
    return VariantAggregateListResponse(
        symbol=symbol,
        variants=[VariantAggregateResponse(**asdict(m)) for m in metrics],
        bot_id=bot_id,
        from_at=from_at,
        to_at=to_at,
    )
