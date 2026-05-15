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
from decimal import Decimal  # runtime: sum_realized_pnl_since returns Decimal (T-525a2)
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
    "select_pnl_peak_and_current",
    "select_recent_closed_trades",
    "sum_realized_pnl_since",
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


async def sum_realized_pnl_since(
    conn: _DbExecutor,
    *,
    table_name: TradeTableName,
    bot_id: str,
    since: datetime,
) -> Decimal:
    """SUM(realized_pnl) of a bot's closed trades since ``since`` (UTC).

    Powers the T-525a2 daily-loss kill-switch gate: ``since`` is UTC-midnight
    of the current trading day, so the sum is the bot's cumulative realized
    P&L for today. Charter invariant inlined (mirror
    :func:`select_recent_closed_trades`): only ``status = 'closed' AND
    realized_pnl IS NOT NULL`` rows count; ``COALESCE(SUM(realized_pnl), 0)``
    so zero matching rows return ``Decimal('0')`` (NEVER ``None`` — the gate
    compares ``total <= -daily_loss_limit_usd`` and must not crash on a fresh
    trading day with no closes yet).

    §5.13 / §N1: ``realized_pnl`` is ``NUMERIC(20,4)`` → asyncpg returns
    ``Decimal``; ``COALESCE(...,0)`` stays NUMERIC → ``Decimal``. NO
    ``float()`` cast anywhere on the P&L path (math-validator Gate 4).

    L-021 SQL-parameter type-cast audit: ``$1`` (``bot_id``) is column-direct
    TEXT equality (``WHERE bot_id = $1``) → L-021-safe, no cast. ``$2``
    (``since``) sits in a **comparison-operator context** (``closed_at >=
    $2``) — exactly the L-021 failure class ("comparison operators across
    unioned types"; the T-537a1 ci-full crash was a ``timestamptz <=``
    mis-inference). The SQL therefore ships an **explicit
    ``$2::timestamptz`` cast** (``closed_at`` is genuinely ``timestamptz``
    per migrations 0005/0008 — the cast type is correct). The mock unit test
    pins ``"$2::timestamptz" in sql`` as a regression guard.

    ``table_name`` is a :data:`TradeTableName` Literal; f-string inlining is
    injection-safe (no arbitrary strings).
    """
    sql = (
        f"SELECT COALESCE(SUM(realized_pnl), 0) FROM {table_name} "  # noqa: S608  # nosec B608
        "WHERE bot_id = $1 AND status = 'closed' AND realized_pnl IS NOT NULL "
        "AND closed_at >= $2::timestamptz"
    )
    row = await conn.fetchrow(sql, bot_id, since)
    if row is None:
        return Decimal("0")
    total = row[0]
    return total if isinstance(total, Decimal) else Decimal(str(total))


async def select_pnl_peak_and_current(
    conn: _DbExecutor,
    *,
    table_name: TradeTableName,
    bot_id: str,
) -> tuple[Decimal, Decimal]:
    """``(peak, current)`` of the bot's LIFETIME cumulative realized P&L.

    Powers the T-525b max-drawdown hard-stop gate:

    * ``current`` = Σ ``realized_pnl`` over ALL the bot's closed trades (the
      final running cumulative — equivalently a plain ``SUM``).
    * ``peak``    = ``MAX`` of the running prefix-sum ordered by
      ``(closed_at, id)`` — the all-time profit high-water mark.

    The gate computes ``drawdown_pct = (peak - current) / peak`` ONLY when
    ``peak > 0`` (you cannot give back profit never earned; the pure-loss
    case is the T-525a2 daily-loss gate's domain). Charter invariant inlined
    (mirror :func:`sum_realized_pnl_since`): only ``status = 'closed' AND
    realized_pnl IS NOT NULL`` rows count. Both values
    ``COALESCE(..., 0)`` so a bot with zero qualifying closed trades returns
    ``(Decimal('0'), Decimal('0'))`` (NEVER ``None``).

    §5.13 / §N1: ``realized_pnl`` is ``NUMERIC(20,4)`` → asyncpg ``Decimal``;
    the window ``SUM(...) OVER (...)`` + the outer ``MAX``/``SUM`` stay
    NUMERIC → ``Decimal``. NO ``float()`` anywhere (math-validator Gate 4).

    The window frame is **explicit** ``ROWS UNBOUNDED PRECEDING`` (NOT the
    default ``RANGE``): with a unique ``(closed_at, id)`` order key there are
    no peer rows so the result is identical, but ``ROWS`` is the
    deterministic running-prefix-sum frame and is defensive against a future
    ORDER BY narrowing.

    L-021 SQL-parameter type-cast audit: the ONLY parameter is ``$1``
    (``bot_id``), used in ``WHERE bot_id = $1`` (appears TWICE — the inner
    window subquery and the outer correlated ``current`` subquery, both
    column-direct TEXT equality) → L-021-safe, no ``::text`` cast. There is
    **no timestamp predicate** (lifetime — no ``closed_at >= $N``) → no
    ``::timestamptz`` cast site (mirror :func:`count_open_trades`; distinct
    from :func:`sum_realized_pnl_since` which DID need the cast for its
    ``closed_at >= $2`` comparison). The doubled ``WHERE`` is byte-identical
    so ``peak`` and ``current`` operate over the same row population.

    ``table_name`` is a :data:`TradeTableName` Literal; f-string inlining is
    injection-safe.
    """
    # Byte-identical charter predicate reused by BOTH the window subquery and
    # the correlated `current` subquery — peak + current MUST operate over the
    # same row population (a divergence would be a correctness bug).
    _where = "WHERE bot_id = $1 AND status = 'closed' AND realized_pnl IS NOT NULL"
    sql = (
        f"SELECT COALESCE(MAX(running), 0) AS peak, "  # noqa: S608  # nosec B608
        f"COALESCE((SELECT SUM(realized_pnl) FROM {table_name} {_where}), 0) AS current "
        f"FROM (SELECT SUM(realized_pnl) OVER ("
        f"ORDER BY closed_at, id ROWS UNBOUNDED PRECEDING) AS running "
        f"FROM {table_name} {_where}) s"
    )
    row = await conn.fetchrow(sql, bot_id)
    if row is None:
        return (Decimal("0"), Decimal("0"))
    peak = row["peak"]
    current = row["current"]
    return (
        peak if isinstance(peak, Decimal) else Decimal(str(peak)),
        current if isinstance(current, Decimal) else Decimal(str(current)),
    )
