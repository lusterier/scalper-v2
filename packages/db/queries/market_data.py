"""market-data-svc query module (§5.10, §7.2).

Owned by ``services/market_data`` (T-100, future); imported by the
T-104 ``OhlcPipeline`` for the closed-candle persistence write.
Raw asyncpg per brief §5.10 ("all queries in hot paths are raw SQL
via asyncpg, parameterized").

The active-symbol-set lookup (``bots`` JOIN ``bot_configs`` per
brief §9.2 line 1454) is **not** in this module — that query lives
with the composition root (T-100 lifespan) and was left out of T-104
per §0.8 (the pipeline accepts ``symbols: list[str]`` at start, no
DB query of its own).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    # asyncpg-stubs splits pool-acquired connections from raw
    # asyncpg.connect() results into nominally-distinct classes that
    # share the structural query surface. Accept either so callers can
    # pass `async with pool.acquire() as conn` results without casting.
    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = [
    "OhlcReplayRow",
    "fetch_latest_ohlc_bucket",
    "insert_ohlc_1m",
    "select_latest_close",
    "select_ohlc_for_replay_window",
]


@dataclass(frozen=True, slots=True)
class OhlcReplayRow:
    """Minimal projection of ``ohlc_1m`` for in-process replay (T-512a).

    Carries only the fields needed to construct
    :class:`packages.bus.schemas.OhlcCandlePayload` for shadow_replay's
    candle handler invocation. Source column intentionally omitted —
    replay consumers don't differentiate provenance (binance vs synthetic
    vs replay) at the FSM step, only at the data-pipeline layer.
    """

    bucket_start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


async def fetch_latest_ohlc_bucket(
    conn: _DbExecutor,
    *,
    symbol: str,
    source: str,
) -> datetime | None:
    """Return the newest persisted ``bucket_start`` for ``(symbol, source)``.

    Used by T-105 backfill to compute the gap between the last stored
    candle and ``now`` before fetching missing 1m klines from Binance
    REST. Returns ``None`` when the symbol has no rows yet (cold-start
    case → caller falls back to a configurable initial-window default).

    Read-only — no idempotency marker required (markers are for external
    writes per §N3).
    """
    # asyncpg's `fetchval` returns Any; the column type is timestamptz
    # which asyncpg yields as `datetime | None` (None on empty result).
    result: datetime | None = await conn.fetchval(
        "SELECT max(bucket_start) FROM ohlc_1m WHERE symbol = $1 AND source = $2",
        symbol,
        source,
    )
    return result


async def insert_ohlc_1m(
    conn: _DbExecutor,
    *,
    symbol: str,
    bucket_start: datetime,
    open: Decimal,  # noqa: A002  # SQL column name; kwarg-only, no positional confusion
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
    source: str,
) -> None:
    """Insert one closed candle into ``ohlc_1m``; idempotent via PK + ON CONFLICT.

    PK ``(symbol, bucket_start, source)`` per migration 0003 / §7.2.
    ON CONFLICT DO UPDATE (last-write-wins, not DO NOTHING) so that
    T-105 backfill via REST ``/api/v3/klines`` can repair a corrupted
    WS-stored row — REST historical is the canonical truth, and a
    bug that mis-classified an in-progress frame as closed must be
    repairable. Closed-bucket WS frames are deterministic per Binance
    spec, so the typical re-write is a no-op against identical values
    (one WAL record per re-write at scale of ~1 candle/min/symbol;
    negligible).

    Genuinely idempotent (same inputs → same row state regardless of
    call count) — no ``@non_idempotent`` marker. Differs from
    :func:`packages.db.queries.signal_gateway.insert_signal`, which
    returns a fresh ``id`` per call and is therefore non-idempotent.

    Caller (the :class:`packages.market.OhlcPipeline` ``_handle``
    method) wraps the call in a try/except so a transient PG error
    (timeout, connection blip) is logged + dropped rather than killing
    the per-symbol consumer task; the next minute's frame for the same
    symbol carries no information about the prior, and T-105 fills
    any gap on the next reconnect or service restart.
    """
    # `open` and `bucket_start` shadow Python builtins / common names
    # only at the parameter scope; the SQL substitution doesn't see
    # those names. Per §5.10 keyword-only by design (no positional
    # confusion across 8 fields).
    await conn.execute(
        """
        INSERT INTO ohlc_1m (
            symbol, bucket_start, open, high, low, close, volume, source
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (symbol, bucket_start, source) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume
        """,
        symbol,
        bucket_start,
        open,
        high,
        low,
        close,
        volume,
        source,
    )


async def select_latest_close(
    conn: _DbExecutor,
    *,
    symbol: str,
    source: str,
) -> Decimal | None:
    """Return latest closed-candle close for ``(symbol, source)`` (T-513a virtual_entry_price).

    SQL: ``SELECT close FROM ohlc_1m WHERE symbol = $1 AND source = $2
    ORDER BY bucket_start DESC LIMIT 1``. Reads from market-data-service-
    populated table (T-104 OhlcPipeline shipped). ``source`` filter is
    REQUIRED — PK is ``(symbol, bucket_start, source)``; multiple sources
    may co-exist (e.g. binance + future synthetic). Mirror sibling
    :func:`fetch_latest_ohlc_bucket` ``(symbol, source)`` convention.
    T-513a caller :func:`services.strategy_engine.app.consumer._resolve_virtual_entry`
    passes ``source="binance"`` (live market data).

    Returns ``None`` when no rows for given ``(symbol, source)`` yet (cold-
    start case → caller falls back to ``Decimal("0")``; worker-side
    defensive filters zero-entry as ``NO_TRIGGER`` immediately on
    observation start).
    """
    row = await conn.fetchrow(
        "SELECT close FROM ohlc_1m WHERE symbol = $1 AND source = $2 "
        "ORDER BY bucket_start DESC LIMIT 1",
        symbol,
        source,
    )
    return row["close"] if row is not None else None


async def select_ohlc_for_replay_window(
    conn: _DbExecutor,
    *,
    symbol: str,
    from_at: datetime,
    to_at: datetime,
    prefetch: int = 1000,
) -> AsyncIterator[OhlcReplayRow]:
    """Server-side cursor on ``ohlc_1m`` for ``[from_at, to_at]`` window (T-512a).

    Used by ``services.execution.app.shadow_replay.replay_shadow_variant_to_now``
    to iterate candles chronologically for in-process FSM replay. Mirror
    T-503 :class:`HistoricalOHLCSource` cursor pattern — bounded memory
    across multi-hour windows. Caller MUST consume the iterator inside
    the same conn.transaction() block (server-side cursor lifetime).

    Source column ignored — replay consumes whatever provenance is in the
    table for the requested symbol+window. ``source = ANY(...)`` filtering
    can be added if specific provenance becomes load-bearing in F5+.
    """
    async with conn.transaction():
        async for row in conn.cursor(
            "SELECT bucket_start, open, high, low, close, volume "
            "FROM ohlc_1m "
            "WHERE symbol = $1 AND bucket_start >= $2 AND bucket_start <= $3 "
            "ORDER BY bucket_start ASC",
            symbol,
            from_at,
            to_at,
            prefetch=prefetch,
        ):
            yield OhlcReplayRow(
                bucket_start=row["bucket_start"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            )
