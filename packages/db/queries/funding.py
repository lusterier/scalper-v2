"""funding-fee query module (T-532a; ADR-0011 funding-fee tracking).

Single writer for the ``funding_fees`` hypertable (migration 0021). The
execution-service APScheduler funding-poll tick (T-532b — operator OQ-4=A,
a SEPARATE tick mirroring the T-531 equity_snapshot tick) calls
:func:`insert_funding_fee` once per settlement with each
:class:`packages.exchange.types.FundingFee` from
``ExchangeClient.get_funding_fees_window()`` (T-532a).

Mirror :func:`packages.db.queries.equity.insert_equity_snapshot` /
:func:`packages.db.queries.execution.insert_trading_event` — append-only
hypertable INSERT, ``@non_idempotent``, surrogate ``id`` auto-populated by
``Identity(always=False)`` (omitted from the INSERT column list).

Writer-only by design: T-532b is the producer; the T-220 cumulative-delta
audit reader (a SEPARATE cumulative funding term, operator OQ-3=A —
H-017-clean, never folded into ``trades.realized_pnl``) is wired in T-532b
— no select helper here per §0 scope discipline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from packages.core import non_idempotent

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = ["insert_funding_fee"]


@non_idempotent
async def insert_funding_fee(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    settled_at: datetime,
    funding: Decimal,
) -> None:
    """INSERT one ``funding_fees`` row (T-532a).

    ``@non_idempotent`` (§N3): append-only audit-grade time-series row, no
    replay-safe key (mirror :func:`insert_equity_snapshot` /
    :func:`insert_trading_event`); the T-532b APScheduler poll tick does NOT
    retry (ADR-0007 D7 — a misfire writes at most one near-dup row,
    surrogate-``id``-distinct; NOT a financial-truth path — truth is the
    T-220 cumulative-delta audit per ADR-0006).

    All 4 ``$N`` are column-direct in ``VALUES`` — **L-021 N/A**: no
    parameter sits in a comparison / CASE / arithmetic / function-arg
    context, so no ``::type`` cast is required (mirror
    :func:`insert_equity_snapshot`). ``id`` is omitted (auto via
    ``Identity(always=False)``). ``funding`` (signed ``Decimal``) lands in
    ``NUMERIC(20,4)`` → PostgreSQL round-half-even to scale 4 (documented,
    repo USD-money convention; not a silent degradation).
    """
    await conn.execute(
        """
        INSERT INTO funding_fees (bot_id, symbol, settled_at, funding)
        VALUES ($1, $2, $3, $4)
        """,
        bot_id,
        symbol,
        settled_at,
        funding,
    )
