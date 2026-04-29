"""§12.1 PaperExchange paper_* persistence helpers (T-213b).

INSERT/UPDATE/DELETE/SELECT helpers used by PaperExchange to land
fill events into the migration-0008 paper_* schema (T-212).

§9.5 step 8 single-tx invariant: callers wrap these helpers in
``async with conn.transaction(): ...`` so paper_orders + paper_trades +
paper_executions + paper_positions persist atomically.

§N3 markers carried by the public ``PaperExchange`` methods
(``@non_idempotent place_market_order``, ``@idempotent set_trading_stop``,
``@idempotent cancel_order``, ``@idempotent set_leverage``); private
helpers compose under callers' markers per
``packages/db/queries/signal_gateway.py`` precedent. The 4 INSERT helpers
also carry ``@non_idempotent`` for grep-friendliness on row-creation
sites; UPDATE/DELETE helpers are not individually decorated.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from packages.core import non_idempotent

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = [
    "close_paper_trade",
    "delete_paper_position",
    "insert_paper_execution",
    "insert_paper_order",
    "insert_paper_position",
    "insert_paper_trade",
    "select_paper_execution_price_by_order_id",
    "select_paper_positions",
    "select_paper_positions_for_hydrate",
    "sum_paper_trades_realized_pnl",
    "update_paper_order_cancelled",
    "update_paper_position_partial",
    "update_paper_position_sl_tp",
    "update_paper_trade_partial",
]


@non_idempotent
async def insert_paper_order(
    conn: _DbExecutor,
    *,
    bot_id: str,
    correlation_id: str,
    exchange_order_id: str,
    symbol: str,
    side: Literal["buy", "sell"],
    order_type: str,
    qty: Decimal,
    price: Decimal | None,
    status: str,
    requested_at: datetime,
    idempotent_flag: bool,
) -> int:
    """Insert one row into paper_orders; return generated id.

    ``exchange='paper'`` discriminator written here per Decision #4.
    ``idempotent_flag`` per Decision #3 mapping table:

    * market (open/close) → False (mirror @non_idempotent + H-003).
    * sl/tp synthetic → True (mirror Bybit set_trading_stop @idempotent).
    """
    row = await conn.fetchrow(
        """
        INSERT INTO paper_orders (
            bot_id, correlation_id, exchange_order_id, exchange,
            symbol, side, order_type, qty, price, status,
            requested_at, idempotent, meta
        )
        VALUES ($1, $2, $3, 'paper', $4, $5, $6, $7, $8, $9, $10, $11, '{}'::jsonb)
        RETURNING id
        """,
        bot_id,
        correlation_id,
        exchange_order_id,
        symbol,
        side,
        order_type,
        qty,
        price,
        status,
        requested_at,
        idempotent_flag,
    )
    if row is None:
        msg = "INSERT paper_orders ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])


@non_idempotent
async def insert_paper_trade(
    conn: _DbExecutor,
    *,
    bot_id: str,
    open_order_id: int,
    symbol: str,
    side: Literal["buy", "sell"],
    entry_price: Decimal,
    qty: Decimal,
    notional_usd: Decimal,
    fees_paid: Decimal,
    opened_at: datetime,
) -> int:
    """Insert one row into paper_trades (status='open'); return generated id.

    ``notional_usd = qty * entry_price`` quantised to NUMERIC(20,4) per
    Decision #3 / BLOCKER 3 fix; NOT NULL on schema.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO paper_trades (
            bot_id, open_order_id, symbol, side, entry_price, qty,
            notional_usd, fees_paid, opened_at, status, meta
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'open', '{}'::jsonb)
        RETURNING id
        """,
        bot_id,
        open_order_id,
        symbol,
        side,
        entry_price,
        qty,
        notional_usd,
        fees_paid,
        opened_at,
    )
    if row is None:
        msg = "INSERT paper_trades ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])


@non_idempotent
async def insert_paper_execution(
    conn: _DbExecutor,
    *,
    exchange_exec_id: str,
    order_id: int,
    trade_id: int,
    bot_id: str,
    symbol: str,
    side: Literal["buy", "sell"],
    price: Decimal,
    qty: Decimal,
    fee: Decimal,
    exec_type: str,
    executed_at: datetime,
) -> None:
    """Insert one row into paper_executions hypertable. Composite PK; no id returned."""
    await conn.execute(
        """
        INSERT INTO paper_executions (
            exchange_exec_id, order_id, trade_id, bot_id, symbol, side,
            price, qty, fee, exec_type, executed_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        exchange_exec_id,
        order_id,
        trade_id,
        bot_id,
        symbol,
        side,
        price,
        qty,
        fee,
        exec_type,
        executed_at,
    )


@non_idempotent
async def insert_paper_position(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    trade_id: int,
    side: Literal["buy", "sell"],
    entry_price: Decimal,
    qty: Decimal,
    remaining_qty: Decimal,
    updated_at: datetime,
) -> None:
    """Insert one row into paper_positions (composite PK on (bot_id, symbol))."""
    await conn.execute(
        """
        INSERT INTO paper_positions (
            bot_id, symbol, trade_id, side, entry_price, qty, remaining_qty, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        bot_id,
        symbol,
        trade_id,
        side,
        entry_price,
        qty,
        remaining_qty,
        updated_at,
    )


async def close_paper_trade(
    conn: _DbExecutor,
    *,
    trade_id: int,
    exit_price: Decimal,
    realized_pnl: Decimal,
    fees_paid: Decimal,
    closed_at: datetime,
    close_reason: str,
    close_order_id: int,
) -> None:
    """UPDATE paper_trades SET ... WHERE id = trade_id (H-018 PK-only).

    Full-close path (status='closed'). H-018 invariant pinned via white-box
    test in ``test_paper_emission.py::test_close_paper_trade_uses_pk_not_symbol_status``.
    """
    await conn.execute(
        """
        UPDATE paper_trades
        SET exit_price = $1, realized_pnl = $2, fees_paid = $3,
            closed_at = $4, close_reason = $5, close_order_id = $6,
            status = 'closed'
        WHERE id = $7
        """,
        exit_price,
        realized_pnl,
        fees_paid,
        closed_at,
        close_reason,
        close_order_id,
        trade_id,
    )


async def update_paper_trade_partial(
    conn: _DbExecutor,
    *,
    trade_id: int,
    new_qty: Decimal,
    new_fees_paid: Decimal,
    new_realized_pnl: Decimal,
) -> None:
    """UPDATE paper_trades SET qty, fees_paid, realized_pnl WHERE id (PK).

    Partial-close path (paper_trades stays OPEN with reduced qty per
    Decision #9). H-018 PK-keyed only.
    """
    await conn.execute(
        """
        UPDATE paper_trades
        SET qty = $1, fees_paid = $2, realized_pnl = $3
        WHERE id = $4
        """,
        new_qty,
        new_fees_paid,
        new_realized_pnl,
        trade_id,
    )


async def update_paper_position_sl_tp(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    sl_price: Decimal | None,
    tp_price: Decimal | None,
    updated_at: datetime,
) -> None:
    """UPDATE paper_positions SET sl_price, tp_price WHERE (bot_id, symbol) PK.

    Decision #15 / BLOCKER 1: ONLY sl_price + tp_price columns persist
    (schema-parity with live position_state per §3.1 line 268). tpsl_mode +
    tp_size live in ``_active_positions`` dict — adapter is source of truth.
    """
    await conn.execute(
        """
        UPDATE paper_positions
        SET sl_price = $1, tp_price = $2, updated_at = $3
        WHERE bot_id = $4 AND symbol = $5
        """,
        sl_price,
        tp_price,
        updated_at,
        bot_id,
        symbol,
    )


async def update_paper_position_partial(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    new_remaining_qty: Decimal,
    tp_hit: bool,
    updated_at: datetime,
) -> None:
    """UPDATE paper_positions SET remaining_qty, tp_hit, updated_at WHERE (bot_id, symbol) PK."""
    await conn.execute(
        """
        UPDATE paper_positions
        SET remaining_qty = $1, tp_hit = $2, updated_at = $3
        WHERE bot_id = $4 AND symbol = $5
        """,
        new_remaining_qty,
        tp_hit,
        updated_at,
        bot_id,
        symbol,
    )


async def delete_paper_position(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
) -> None:
    """DELETE FROM paper_positions WHERE bot_id = $1 AND symbol = $2 (composite PK)."""
    await conn.execute(
        "DELETE FROM paper_positions WHERE bot_id = $1 AND symbol = $2",
        bot_id,
        symbol,
    )


async def update_paper_order_cancelled(
    conn: _DbExecutor,
    *,
    order_id: int,
    bot_id: str,
) -> None:
    """UPDATE paper_orders SET status='cancelled' WHERE id = $1 AND bot_id = $2.

    Idempotent: UPDATE on already-cancelled row affects 0 rows; returns silently.
    """
    await conn.execute(
        "UPDATE paper_orders SET status = 'cancelled' WHERE id = $1 AND bot_id = $2",
        order_id,
        bot_id,
    )


# T-213c — read helpers (SELECT shape; no idempotency markers — read-only).


async def select_paper_positions_for_hydrate(
    conn: _DbExecutor,
    *,
    bot_id: str,
) -> list[asyncpg.Record]:
    """SELECT all open paper_positions for ``bot_id`` joined with paper_trades
    + the OPEN execution's fee from paper_executions.

    T-213c restart-recovery hydrate query. Returns one row per active
    position with all fields needed to reconstruct ``_active_positions``
    dict modulo OQ-2 default B (tpsl_mode + tp_size NOT in schema;
    defaulted to 'Full' / None at adapter level).

    Uses inner JOIN on ``paper_executions.exec_type = 'open'`` per
    Decision #5 — the open execution's fee is the canonical entry_fee
    needed for full-close realized_pnl computation post-restart (OQ-1
    default A regression guard).
    """
    return await conn.fetch(
        """
        SELECT
            pp.symbol, pp.trade_id, pp.side, pp.entry_price, pp.qty,
            pp.remaining_qty, pp.sl_price, pp.tp_price, pp.tp_hit,
            pt.fees_paid, pt.open_order_id,
            pe.fee AS entry_fee
        FROM paper_positions pp
        JOIN paper_trades pt ON pt.id = pp.trade_id
        JOIN paper_executions pe ON pe.order_id = pt.open_order_id
                                 AND pe.exec_type = 'open'
        WHERE pp.bot_id = $1
        ORDER BY pp.symbol
        """,
        bot_id,
    )


async def select_paper_positions(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str | None = None,
) -> list[asyncpg.Record]:
    """SELECT paper_positions for ``bot_id`` (optionally filtered by symbol).

    Used by ``PaperExchange.get_positions``. Returns rows shaped for
    ``Position`` dataclass mapping. ``Position.size`` consumes
    ``remaining_qty`` per OQ-4 default A.
    """
    if symbol is None:
        return await conn.fetch(
            """
            SELECT symbol, side, remaining_qty, entry_price
            FROM paper_positions
            WHERE bot_id = $1
            ORDER BY symbol
            """,
            bot_id,
        )
    return await conn.fetch(
        """
        SELECT symbol, side, remaining_qty, entry_price
        FROM paper_positions
        WHERE bot_id = $1 AND symbol = $2
        """,
        bot_id,
        symbol,
    )


async def select_paper_execution_price_by_order_id(
    conn: _DbExecutor,
    *,
    exchange_order_id: str,
) -> Decimal | None:
    """SELECT the chronologically-first paper_executions.price for a given
    paper_orders.exchange_order_id. Returns None if no match.

    Used by ``PaperExchange.get_fill_price``. Decision #7 — LIMIT 1
    ORDER BY executed_at ASC defends against schema drift if a future
    task synthesises additional executions per order.
    """
    row = await conn.fetchrow(
        """
        SELECT pe.price FROM paper_executions pe
        JOIN paper_orders po ON po.id = pe.order_id
        WHERE po.exchange_order_id = $1
        ORDER BY pe.executed_at ASC
        LIMIT 1
        """,
        exchange_order_id,
    )
    if row is None:
        return None
    price: Decimal = row["price"]
    return price


async def sum_paper_trades_realized_pnl(
    conn: _DbExecutor,
    *,
    bot_id: str,
) -> Decimal:
    """SUM(realized_pnl) over closed paper_trades for ``bot_id``.

    Used by ``PaperExchange.get_closed_pnl_cumulative``. Decision #8 —
    COALESCE NULL → Decimal("0") so consumers see numeric, not None.
    """
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(realized_pnl), 0) AS total
        FROM paper_trades
        WHERE bot_id = $1 AND status = 'closed'
        """,
        bot_id,
    )
    if row is None:
        return Decimal("0")
    total = row["total"]
    if isinstance(total, Decimal):
        return total
    return Decimal(total)
