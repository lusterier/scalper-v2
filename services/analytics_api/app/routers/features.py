"""``/api/features/*`` read endpoints (T-404, BRIEF §9.6:1627 + §14.3:2065).

Two endpoints:

* ``GET /api/features/latest`` — name-prefix-filtered KV view, one row
  per (feature_name, symbol). Pagination via ``?limit=`` (1..500,
  default 100) + ``?offset=`` (≥0). Empty / missing ``?prefix=`` →
  no filter.
* ``GET /api/features/history`` — time-series for one (feature_name,
  symbol) pair over ``computed_at`` range. ``feature_name`` + ``symbol``
  are required Query params (not Path params per OQ-2 default A — avoids
  URL-encoding pitfalls of feature_name dots like ``ind.btcusdt.15m.ema_20``).
  Pagination via ``?limit=`` (1..5000, default 1000 — chart-resolution
  scale) + ``?offset=`` (≥0). ``?from=`` / ``?to=`` ISO-8601 range
  optional (half-open: from inclusive, to exclusive). 200 with empty
  list when no rows in range — collection-shape semantics, NOT 404.

Per OQ-6 default C: NO staleness flag. UI computes ``stale = age >
threshold`` client-side per its own per-feature policy; backend stays
policy-free.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — FastAPI inspects Query[datetime] at runtime
from typing import Annotated

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, Query

from packages.db.queries.analytics import (
    count_features_history,
    count_latest_features,
    select_features_history,
    select_latest_features,
)

from ..deps import get_pool
from ..models.features import (
    FeatureHistoryListResponse,
    FeatureLatestListResponse,
    FeatureResponse,
)

__all__ = ["router"]


router = APIRouter(prefix="/api/features", tags=["features"])


_LATEST_DEFAULT_LIMIT = 100
_LATEST_MAX_LIMIT = 500
_HISTORY_DEFAULT_LIMIT = 1000
_HISTORY_MAX_LIMIT = 5000


@router.get("/latest", response_model=FeatureLatestListResponse)
async def list_latest_features(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    prefix: Annotated[
        str | None,
        Query(description="Filter by feature_name prefix; empty / omitted = no filter."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_LATEST_MAX_LIMIT)] = _LATEST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FeatureLatestListResponse:
    """Latest value per (feature_name, symbol); name-prefix filtered."""
    async with pool.acquire() as conn:
        rows = await select_latest_features(
            conn,
            prefix=prefix,
            limit=limit,
            offset=offset,
        )
        total = await count_latest_features(conn, prefix=prefix)
    return FeatureLatestListResponse(
        features=[FeatureResponse(**asdict(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/history", response_model=FeatureHistoryListResponse)
async def list_feature_history(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    feature_name: Annotated[str, Query(min_length=1)],
    symbol: Annotated[str, Query(min_length=1)],
    from_at: Annotated[
        datetime | None,
        Query(alias="from", description="computed_at >= from (ISO-8601, inclusive)."),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(alias="to", description="computed_at < to (ISO-8601, exclusive)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_HISTORY_MAX_LIMIT)] = _HISTORY_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FeatureHistoryListResponse:
    """Time-series for one (feature_name, symbol) pair (ORDER BY computed_at DESC)."""
    async with pool.acquire() as conn:
        rows = await select_features_history(
            conn,
            feature_name=feature_name,
            symbol=symbol,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            offset=offset,
        )
        total = await count_features_history(
            conn,
            feature_name=feature_name,
            symbol=symbol,
            from_at=from_at,
            to_at=to_at,
        )
    return FeatureHistoryListResponse(
        features=[FeatureResponse(**asdict(r)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
