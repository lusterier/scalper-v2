"""Startup warmup loader for feature-engine (§9.3 line 1500, T-110d).

For each registered ``(symbol, interval)`` key, query the last
``max(feature.warmup_candles for _, feature in features)`` rows from
the matching ``ohlc_*`` source via T-110b
:func:`~packages.db.queries.feature_engine.fetch_warmup_window` and
push them into the T-110a :class:`~packages.features.buffers.BufferRegistry`
in chronological order so the first live NATS frame on each key hits
a warmed buffer.

Per-key error isolation: a ``fetch_warmup_window`` failure on key A
logs ``feature_warmup_load_error`` and continues to key B —
under-fill on A is recoverable on subsequent live frames
(:meth:`Feature.compute` raises
:class:`~packages.features.errors.FeatureUnderflowError` until enough
candles accumulate). Fail-fast would unnecessarily block the entire
service on a single transient DB error during startup.

OhlcRow tuple-field order assumption (T-110b Write-time guidance #3):
``(symbol, bucket_start, open, high, low, close, volume, source)``.
:func:`test_warmup_load_ohlc_row_field_order_locked` asserts this so a
future T-110b refactor that reorders the tuple cannot silently break
the warmup mapping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from packages.db.queries.feature_engine import fetch_warmup_window
from packages.features.types import OhlcCandle

if TYPE_CHECKING:
    from collections.abc import Mapping

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.features.buffers import BufferRegistry
    from packages.features.protocols import Feature


__all__ = ["warmup_load"]


async def warmup_load(
    *,
    pool: asyncpg.Pool[asyncpg.Record],
    registry: BufferRegistry,
    features_by_key: Mapping[tuple[str, str], list[tuple[str, Feature]]],
    source: str,
    logger: BoundLogger,
) -> None:
    """Prime ``registry`` with cagg history for every registered key.

    Empty ``features_by_key`` = no-op (empty registry deployments
    stay healthy + ready emitting only default Prometheus collectors).
    Caller MUST have invoked
    :meth:`packages.features.buffers.BufferRegistry.acquire` (i.e.,
    :meth:`FeaturePipeline.acquire_handles`) BEFORE this function so
    the buffers are allocated; otherwise
    :meth:`BufferRegistry.push` is a silent no-op (T-110a Decision #2).
    """
    for (symbol, interval), entries in features_by_key.items():
        n = max(feature.warmup_candles for _, feature in entries)
        try:
            async with pool.acquire() as conn:
                rows = await fetch_warmup_window(
                    conn,
                    symbol=symbol,
                    interval=interval,
                    n=n,
                    source=source,
                )
        except Exception as exc:
            logger.error(
                "feature_warmup_load_error",
                symbol=symbol,
                interval=interval,
                error=str(exc),
            )
            continue
        for row in rows:
            registry.push(
                symbol,
                interval,
                OhlcCandle(
                    symbol=row[0],
                    interval=interval,
                    bucket_start=row[1],
                    open=row[2],
                    high=row[3],
                    low=row[4],
                    close=row[5],
                    volume=row[6],
                    source=row[7],
                ),
            )
        logger.info(
            "feature_warmup_loaded",
            symbol=symbol,
            interval=interval,
            requested=n,
            loaded=len(rows),
        )
