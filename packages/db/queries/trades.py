"""Trades read helpers for strategy-engine cooldown gate (T-526).

Distinct from :mod:`packages.db.queries.analytics` which serves the analytics-api
read endpoints (ORDER BY ASC, limit-less, multi-field projection). This module
serves the per-signal cooldown gate (ORDER BY DESC, LIMIT N, minimal projection).

Charter invariant mirrored from :func:`packages.db.queries.analytics.select_trades_for_analytics`
verbatim: only ``status = 'closed' AND realized_pnl IS NOT NULL`` rows count for
cooldown (open trades have ``realized_pnl=NULL`` per schema; including them would
break the streak walk loop in :mod:`services.strategy_engine.app.cooldown_gate`).

Live vs paper dispatch: ``exchange_mode`` literal selects the source table
(``trades`` for live/testnet bots, ``paper_trades`` for paper bots; each bot is
one mode by ``BotConfig.exchange.mode``). Table-name selection is via
``Literal``-typed dispatcher, NOT raw operator input — no SQL-injection surface.

L-021 SQL-parameter type-cast audit: both ``$1`` (``bot_id`` used in
``WHERE bot_id = $1`` — direct column equality on TEXT column) and ``$2``
(used in ``LIMIT $2`` — direct LIMIT clause) sit in L-021-safe column-direct /
LIMIT-direct contexts. No explicit ``::text`` / ``::int`` cast needed; asyncpg
inference is unambiguous in these positions.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal  # noqa: TC003 — runtime annotation on @dataclass slot
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = [
    "ClosedTradeRow",
    "TradeTableName",
    "count_open_trades",
    "select_recent_closed_trades",
]


type TradeTableName = Literal["trades", "paper_trades"]


@dataclass(frozen=True, slots=True)
class ClosedTradeRow:
    """Minimal closed-trade projection for cooldown-gate streak walk.

    Two fields only: ``realized_pnl`` (Decimal; loss = ``< 0`` strict per T-526
    OQ-2=A) + ``closed_at`` (tz-aware datetime; cooldown-until computed via
    ``closed_at + timedelta(minutes=cfg.*_minutes)``).

    Distinct from :class:`packages.db.queries.analytics.TradeRealizedPnlRow`
    (3 fields incl. ``bot_id``) — cooldown gate queries within a single bot
    and doesn't need the bot_id back.
    """

    realized_pnl: Decimal
    closed_at: datetime


async def select_recent_closed_trades(
    conn: _DbExecutor,
    *,
    bot_id: str,
    table_name: TradeTableName,
    limit: int,
) -> list[ClosedTradeRow]:
    """Top-``limit`` closed trades for ``bot_id`` ordered by ``closed_at`` DESC.

    Charter invariant inlined: ``WHERE status = 'closed' AND realized_pnl IS NOT NULL``
    (mirror :func:`packages.db.queries.analytics.select_trades_for_analytics`).
    Open trades (``status='open'``, ``realized_pnl=NULL``) are excluded so the
    cooldown-gate streak-walk loop can rely on every returned row having a
    finalized non-null ``realized_pnl``.

    ORDER BY ``closed_at DESC, id DESC`` deterministic tie-break: multiple
    trades closing in the same microsecond (rare but possible under partial-TP
    fan-out) get a stable order. Mirror analytics paginated pattern.

    ``table_name`` is a :data:`TradeTableName` Literal (compile-time-checked
    static membership in ``{"trades", "paper_trades"}``); NOT raw user input.
    Inlining via f-string is safe (no SQL-injection surface — Literal type
    forbids arbitrary strings).
    """
    sql = (
        f"SELECT realized_pnl, closed_at FROM {table_name} "  # noqa: S608  # nosec B608
        "WHERE bot_id = $1 AND status = 'closed' AND realized_pnl IS NOT NULL "
        "ORDER BY closed_at DESC, id DESC "
        "LIMIT $2"
    )
    rows = await conn.fetch(sql, bot_id, limit)
    return [
        ClosedTradeRow(
            realized_pnl=row["realized_pnl"],
            closed_at=row["closed_at"],
        )
        for row in rows
    ]


async def count_open_trades(
    conn: _DbExecutor,
    *,
    table_name: TradeTableName,
    bot_id: str | None,
) -> int:
    """COUNT(*) of open positions for the T-524 concurrent-trades caps gate.

    Charter invariant inlined: ``WHERE status = 'open'``. Per-bot count
    (``bot_id`` given) adds ``AND bot_id = $1``; global count (``bot_id`` is
    ``None``) has NO bot_id predicate (counts every open position in
    ``table_name`` across all bots in this exchange-mode realm — paper bots
    count ``paper_trades``, live/testnet bots count ``trades`` per T-524
    OQ-3=A per-exchange-mode-realm semantic).

    A position is "open" iff ``status = 'open'`` (schema has only
    ``open``/``closed``; a partially-closed position keeps ``status='open'``
    through partial TP and still consumes a concurrent slot — counted, per
    T-524 OQ-4 default A). Distinct from
    :func:`select_recent_closed_trades` which is the ``status = 'closed'``
    side for the T-526 cooldown gate.

    L-021 SQL-parameter type-cast audit: the only parameter is ``$1``
    (``bot_id``), used in ``WHERE bot_id = $1`` — direct column equality on a
    TEXT column → L-021-safe; no ``::text`` cast needed (asyncpg inference is
    unambiguous in column-direct equality position). No other ``$N``
    parameters exist: the caps comparison (``count >= cap``) is a Python-side
    int compare in :mod:`services.strategy_engine.app.concurrent_caps_gate`,
    NOT a SQL ``LIMIT``/arithmetic bind. No timestamp predicate exists
    (``status`` + optional ``bot_id`` only) → no ``::timestamptz`` cast site.

    ``table_name`` is a :data:`TradeTableName` Literal (compile-time-checked
    membership in ``{"trades", "paper_trades"}``); NOT raw user input.
    Inlining via f-string is safe (no SQL-injection surface).
    """
    if bot_id is None:
        sql = (
            f"SELECT count(*) FROM {table_name} "  # noqa: S608  # nosec B608
            "WHERE status = 'open'"
        )
        row = await conn.fetchrow(sql)
    else:
        sql = (
            f"SELECT count(*) FROM {table_name} "  # noqa: S608  # nosec B608
            "WHERE bot_id = $1 AND status = 'open'"
        )
        row = await conn.fetchrow(sql, bot_id)
    return int(row[0]) if row is not None else 0
