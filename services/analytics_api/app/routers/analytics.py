"""``/api/analytics/*`` aggregates + Monte-Carlo (T-406, BRIEF §9.6:1628 + §14.3:2060).

4 endpoints:

* ``GET /api/analytics/expectancy`` — single-row expectancy + WR + counts
* ``GET /api/analytics/heatmap/hourly`` — 24x7 hourxweekday grid (168 cells)
* ``GET /api/analytics/pnl-series`` — cumulative time-series; bucket=hour|day;
  capped at 5000 points (PRE-VALIDATE per WG#7 — 422 BEFORE DB query)
* ``POST /api/analytics/monte-carlo`` — bootstrap MC; CPU-heavy via
  ``asyncio.to_thread``; 5-min in-memory cache per BRIEF §9.6:1641

WG#2: MC endpoint releases pool connection BEFORE asyncio.to_thread
compute. WG#6: MC seed derived deterministically from request shape
(SHA256-truncated 64-bit int) → reproducible + cache-coherent.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import (
    Callable,  # noqa: TC003 — FastAPI inspects Annotated[Callable[...], Depends(...)] at runtime
)
from dataclasses import asdict
from datetime import datetime  # noqa: TC003 — FastAPI inspects Query[datetime] at runtime
from typing import Annotated, Literal

import asyncpg  # noqa: TC002 — FastAPI inspects Annotated[asyncpg.Pool, Depends(...)] at runtime
from fastapi import APIRouter, Depends, HTTPException, Query, status

from packages.db.queries.analytics import select_trades_for_analytics

from ..analytics_cache import AnalyticsCache, cache_key
from ..analytics_compute import (
    PnlBucket,
    compute_expectancy,
    compute_hourly_heatmap,
    compute_monte_carlo,
    compute_pnl_series,
)
from ..deps import get_analytics_cache, get_now_fn, get_pool
from ..models.analytics import (
    ExpectancyResponse,
    HeatmapCellResponse,
    HeatmapResponse,
    MonteCarloResponse,
    PnlSeriesPointResponse,
    PnlSeriesResponse,
)

__all__ = ["router"]


router = APIRouter(prefix="/api/analytics", tags=["analytics"])


_PNL_SERIES_MAX_POINTS = 5000
_MC_DEFAULT_SIMULATIONS = 1000
_MC_MAX_SIMULATIONS = 10000
_CACHE_TTL_SECONDS = 300


def _derive_mc_seed(
    bot_id: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
    n_simulations: int,
) -> int:
    """SHA256-truncated 64-bit int seed; deterministic per request shape (WG#6).

    Same inputs → same seed → reproducible MC across calls (cache-coherent
    + debug-friendly). 64-bit space sufficient for collision-resistance at
    ~10k unique requests.
    """
    parts = [str(bot_id or ""), str(from_at or ""), str(to_at or ""), str(n_simulations)]
    raw = "|".join(parts).encode("utf-8")
    digest = hashlib.sha256(raw).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=False)


@router.get("/expectancy", response_model=ExpectancyResponse)
async def get_expectancy(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    bot_id: Annotated[str | None, Query()] = None,
    from_at: Annotated[
        datetime | None,
        Query(alias="from", description="closed_at >= from (ISO-8601, inclusive)."),
    ] = None,
    to_at: Annotated[
        datetime | None,
        Query(alias="to", description="closed_at < to (ISO-8601, exclusive)."),
    ] = None,
) -> ExpectancyResponse:
    """Standard expectancy + WR + counts over (bot_id, from, to) window."""
    async with pool.acquire() as conn:
        rows = await select_trades_for_analytics(
            conn,
            bot_id=bot_id,
            from_at=from_at,
            to_at=to_at,
        )
    metrics = compute_expectancy(rows)
    return ExpectancyResponse(
        **asdict(metrics),
        bot_id=bot_id,
        from_at=from_at,
        to_at=to_at,
    )


@router.get("/heatmap/hourly", response_model=HeatmapResponse)
async def get_hourly_heatmap(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    bot_id: Annotated[str | None, Query()] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
) -> HeatmapResponse:
    """24x7 hourxweekday grid (168 cells) of (trade_count, avg_pnl)."""
    async with pool.acquire() as conn:
        rows = await select_trades_for_analytics(
            conn,
            bot_id=bot_id,
            from_at=from_at,
            to_at=to_at,
        )
    cells = compute_hourly_heatmap(rows)
    return HeatmapResponse(
        cells=[HeatmapCellResponse(**asdict(c)) for c in cells],
        bot_id=bot_id,
        from_at=from_at,
        to_at=to_at,
    )


@router.get("/pnl-series", response_model=PnlSeriesResponse)
async def get_pnl_series(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    bot_id: Annotated[str | None, Query()] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    bucket: Annotated[
        Literal["hour", "day"],
        Query(description="Time bucket size for cumulative series."),
    ] = "day",
) -> PnlSeriesResponse:
    """Cumulative P&L time-series; PRE-VALIDATE 5000-point cap per WG#7."""
    # WG#7: pre-validate window before DB query to avoid loading huge rowsets
    # only to discard. Estimate (to_at - from_at) / bucket_size > cap → 422.
    if from_at is not None and to_at is not None:
        delta = to_at - from_at
        bucket_seconds = 3600 if bucket == "hour" else 86400
        estimated_buckets = int(delta.total_seconds() // bucket_seconds) + 1
        if estimated_buckets > _PNL_SERIES_MAX_POINTS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"window estimated to produce {estimated_buckets} {bucket} buckets; "
                    f"max {_PNL_SERIES_MAX_POINTS}"
                ),
            )

    async with pool.acquire() as conn:
        rows = await select_trades_for_analytics(
            conn,
            bot_id=bot_id,
            from_at=from_at,
            to_at=to_at,
        )
    points = compute_pnl_series(rows, bucket=cast_bucket(bucket))
    return PnlSeriesResponse(
        points=[PnlSeriesPointResponse(**asdict(p)) for p in points],
        bot_id=bot_id,
        from_at=from_at,
        to_at=to_at,
        bucket=bucket,
    )


def cast_bucket(b: Literal["hour", "day"]) -> PnlBucket:
    """Pass-through cast — Literal type alignment between FastAPI Query + compute module."""
    return b


@router.post("/monte-carlo", response_model=MonteCarloResponse)
async def post_monte_carlo(
    pool: Annotated[asyncpg.Pool, Depends(get_pool)],
    cache: Annotated[AnalyticsCache, Depends(get_analytics_cache)],
    now_fn: Annotated[Callable[[], datetime], Depends(get_now_fn)],
    bot_id: Annotated[str | None, Query()] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    n_simulations: Annotated[
        int,
        Query(ge=1, le=_MC_MAX_SIMULATIONS),
    ] = _MC_DEFAULT_SIMULATIONS,
) -> MonteCarloResponse:
    """Bootstrap MC; CPU-heavy via asyncio.to_thread; 5-min in-memory cache."""
    key = cache_key(
        "monte-carlo",
        {"bot_id": bot_id, "from_at": from_at, "to_at": to_at, "n_simulations": n_simulations},
    )

    async def _compute() -> MonteCarloResponse:
        # WG#2: release pool connection BEFORE asyncio.to_thread compute.
        async with pool.acquire() as conn:
            rows = await select_trades_for_analytics(
                conn,
                bot_id=bot_id,
                from_at=from_at,
                to_at=to_at,
            )
        # Pool released; CPU-heavy bootstrap on thread pool.
        seed = _derive_mc_seed(bot_id, from_at, to_at, n_simulations)
        result = await asyncio.to_thread(
            compute_monte_carlo,
            rows,
            n_simulations=n_simulations,
            seed=seed,
        )
        return MonteCarloResponse(
            **asdict(result),
            bot_id=bot_id,
            from_at=from_at,
            to_at=to_at,
        )

    return await cache.get_or_compute(  # type: ignore[no-any-return]
        key,
        _CACHE_TTL_SECONDS,
        _compute,
        now_fn=now_fn,
    )
