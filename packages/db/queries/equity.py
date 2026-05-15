"""equity-snapshot query module (§15.3:2161, T-531).

Single writer for the ``bot_equity_snapshots`` hypertable (migration 0019).
The execution-service APScheduler tick (T-531) calls
:func:`insert_equity_snapshot` once per bot per tick with the T-530
``ExchangeClient.get_account_balance()`` result.

Mirror :func:`packages.db.queries.execution.insert_trading_event` —
append-only hypertable INSERT, ``@non_idempotent``, surrogate ``id``
auto-populated by ``Identity(always=False)`` (omitted from the INSERT
column list).

Writer-only by design: T-531 is the producer; the reader (analytics-api
equity endpoint / Grafana) is a later task — no select helper here per §0
scope discipline.
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


__all__ = ["insert_equity_snapshot"]


@non_idempotent
async def insert_equity_snapshot(
    conn: _DbExecutor,
    *,
    bot_id: str,
    snapshot_at: datetime,
    wallet_balance: Decimal,
    available_balance: Decimal,
    total_equity: Decimal,
    margin_balance: Decimal,
    unrealized_pnl: Decimal,
) -> None:
    """INSERT one ``bot_equity_snapshots`` row (§15.3:2161, T-531).

    ``@non_idempotent`` (§N3): append-only audit-grade time-series row, no
    replay-safe key (mirror :func:`insert_trading_event`); the APScheduler
    tick does NOT retry (ADR-0007 D7 — a misfire writes at most one near-dup
    monitoring row, surrogate-``id``-distinct; NOT a financial-truth path —
    truth is the T-220 cumulative-delta audit per ADR-0006).

    All 7 ``$N`` are column-direct in ``VALUES`` — **L-021 N/A**: no
    parameter sits in a comparison / CASE / arithmetic / function-arg
    context, so no ``::type`` cast is required (mirror
    :func:`insert_trading_event`, whose only cast is the JSONB column it
    has and this table does not). ``id`` is omitted (auto via
    ``Identity(always=False)``). The 5 balance ``Decimal`` values land in
    ``NUMERIC(20,4)`` → PostgreSQL round-half-even to scale 4 (documented,
    repo USD-money convention; not a silent degradation).
    """
    await conn.execute(
        """
        INSERT INTO bot_equity_snapshots (
            bot_id, snapshot_at, wallet_balance, available_balance,
            total_equity, margin_balance, unrealized_pnl
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        bot_id,
        snapshot_at,
        wallet_balance,
        available_balance,
        total_equity,
        margin_balance,
        unrealized_pnl,
    )
