"""execution-service query module (§5.10, §7.2).

T-215 ships :func:`select_active_bots` for adapter pool composition.
T-216b extends with placement-tx persistence helpers per §9.5 step 8:

* :func:`insert_order` — orders row INSERT (BIGSERIAL id returned).
* :func:`insert_trade` — trades row INSERT (BIGSERIAL id returned;
  ``realized_pnl`` + ``fees_paid`` NULL initial; T-218/T-219 backfill).
* :func:`insert_position_state` — composite PK ``(bot_id, symbol)``.
* :func:`insert_trading_event` — trading_events hypertable INSERT (§7.2 line 1091).
* :func:`update_trade_close` — UPDATE trades SET status='closed' (H-018 PK-only).
* :func:`delete_position_state` — composite PK delete (flat after close).

Mirror T-213b ``packages/exchange/paper/persistence.py`` pattern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from packages.core import non_idempotent

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = [
    "BotRow",
    "ExchangeMode",
    "delete_position_state",
    "insert_order",
    "insert_position_state",
    "insert_trade",
    "insert_trading_event",
    "select_active_bots",
    "update_trade_close",
]


ExchangeMode = Literal["live", "testnet", "paper"]
_VALID_EXCHANGE_MODES: frozenset[str] = frozenset({"live", "testnet", "paper"})

_SELECT_ACTIVE_BOTS_SQL = """
    SELECT bot_id, display_name, exchange_mode
    FROM bots
    WHERE status = 'active'
    ORDER BY bot_id
"""


@dataclass(frozen=True, slots=True)
class BotRow:
    """Read-only projection of ``bots`` table columns needed for adapter composition."""

    bot_id: str
    display_name: str
    exchange_mode: ExchangeMode


def _validate_exchange_mode(value: str) -> ExchangeMode:
    """Narrow DB Text column to ``ExchangeMode`` literal; raise on unknown.

    Defends against operator typos in the bots table — unknown modes
    crash composition rather than silently route to an undefined branch.
    """
    if value not in _VALID_EXCHANGE_MODES:
        raise ValueError(
            f"unknown exchange_mode {value!r}; expected one of {sorted(_VALID_EXCHANGE_MODES)}"
        )
    return value  # type: ignore[return-value]


async def select_active_bots(conn: _DbExecutor) -> list[BotRow]:
    """Return active bots ordered by ``bot_id`` (deterministic for partial-failure debugability)."""
    rows = await conn.fetch(_SELECT_ACTIVE_BOTS_SQL)
    return [
        BotRow(
            bot_id=str(row["bot_id"]),
            display_name=str(row["display_name"]),
            exchange_mode=_validate_exchange_mode(str(row["exchange_mode"])),
        )
        for row in rows
    ]


# T-216b — placement-tx persistence helpers (§9.5 step 8) ----------------


@non_idempotent
async def insert_order(
    conn: _DbExecutor,
    *,
    bot_id: str,
    signal_id: int | None,
    correlation_id: str,
    exchange_order_id: str,
    exchange: str,
    symbol: str,
    side: Literal["buy", "sell"],
    order_type: str,
    qty: Decimal,
    price: Decimal | None,
    status: str,
    requested_at: datetime,
    filled_at: datetime | None,
    closed_at: datetime | None,
    idempotent_flag: bool,
) -> int:
    """INSERT into ``orders``; return generated BIGSERIAL ``id``.

    ``status`` per §7.2 line 967 enum: ``'requested' | 'placed' | 'filled' |
    'cancelled' | 'rejected' | 'emergency_closed'``. ``idempotent_flag``
    per §N3 marker mapping (market = False per H-003; sl/tp synthetic = True).
    """
    row = await conn.fetchrow(
        """
        INSERT INTO orders (
            bot_id, signal_id, correlation_id, exchange_order_id, exchange,
            symbol, side, order_type, qty, price, status,
            requested_at, filled_at, closed_at, idempotent, meta
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, '{}'::jsonb)
        RETURNING id
        """,
        bot_id,
        signal_id,
        correlation_id,
        exchange_order_id,
        exchange,
        symbol,
        side,
        order_type,
        qty,
        price,
        status,
        requested_at,
        filled_at,
        closed_at,
        idempotent_flag,
    )
    if row is None:
        msg = "INSERT orders ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])


@non_idempotent
async def insert_trade(
    conn: _DbExecutor,
    *,
    bot_id: str,
    signal_id: int | None,
    open_order_id: int,
    symbol: str,
    side: Literal["buy", "sell"],
    entry_price: Decimal,
    qty: Decimal,
    notional_usd: Decimal,
    opened_at: datetime,
) -> int:
    """INSERT into ``trades`` (status='open'); return BIGSERIAL ``id``.

    ``realized_pnl`` and ``fees_paid`` NULL initial (T-218/T-219 backfill
    from execution stream + cumulative-delta close per H-012).
    """
    row = await conn.fetchrow(
        """
        INSERT INTO trades (
            bot_id, signal_id, open_order_id, symbol, side, entry_price,
            qty, notional_usd, opened_at, status, meta
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'open', '{}'::jsonb)
        RETURNING id
        """,
        bot_id,
        signal_id,
        open_order_id,
        symbol,
        side,
        entry_price,
        qty,
        notional_usd,
        opened_at,
    )
    if row is None:
        msg = "INSERT trades ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])


@non_idempotent
async def insert_position_state(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    trade_id: int,
    side: Literal["buy", "sell"],
    entry_price: Decimal,
    qty: Decimal,
    remaining_qty: Decimal,
    sl_price: Decimal | None,
    tp_price: Decimal | None,
    sl_type: str | None,
    updated_at: datetime,
) -> None:
    """INSERT into ``position_state`` (composite PK ``(bot_id, symbol)``)."""
    await conn.execute(
        """
        INSERT INTO position_state (
            bot_id, symbol, trade_id, side, entry_price, qty, remaining_qty,
            sl_price, tp_price, sl_type, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        bot_id,
        symbol,
        trade_id,
        side,
        entry_price,
        qty,
        remaining_qty,
        sl_price,
        tp_price,
        sl_type,
        updated_at,
    )


@non_idempotent
async def insert_trading_event(
    conn: _DbExecutor,
    *,
    occurred_at: datetime,
    bot_id: str | None,
    correlation_id: str | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """INSERT into ``trading_events`` hypertable (§7.2 line 1091)."""
    await conn.execute(
        """
        INSERT INTO trading_events (occurred_at, bot_id, correlation_id, event_type, payload)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        occurred_at,
        bot_id,
        correlation_id,
        event_type,
        json.dumps(payload),
    )


async def update_trade_close(
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
    """UPDATE trades SET ... WHERE id = $1 — H-018 PK-only invariant."""
    await conn.execute(
        """
        UPDATE trades
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


async def delete_position_state(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
) -> None:
    """DELETE from position_state — composite PK ``(bot_id, symbol)``."""
    await conn.execute(
        "DELETE FROM position_state WHERE bot_id = $1 AND symbol = $2",
        bot_id,
        symbol,
    )
