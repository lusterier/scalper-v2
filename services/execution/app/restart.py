"""§9.5 / H-020 / H-026 post-restart reconciliation (T-221).

Runs once at lifespan startup between dispatcher subscribe and start.
Per bot: fetch exchange positions + DB position_state rows; close
orphan_db (DB row, no exchange) with ``reconcile_gone``; market-close
orphan_exchange (exchange position, no DB row) after H-026
race-window guard; spawn monitor task for matching pairs (per H-026 the
match is by ``(bot_id, symbol)`` PK only — never by qty). See
:func:`reconcile_on_startup` for the public entry point.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from packages.db.queries.execution import (
    delete_position_state,
    select_open_order_id_by_trade_id,
    select_position_states_for_bots,
    select_recent_open_trade_exists,
    update_trade_close,
)

from .lifecycle import run_position_monitor_for_trade

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient
    from packages.core import BotId
    from packages.db.queries.execution import PositionStateRow
    from packages.exchange.protocols import ExchangeClient
    from packages.exchange.types import Position


__all__ = ["reconcile_on_startup"]


_OPPOSITE_SIDE: dict[str, str] = {"buy": "sell", "sell": "buy"}


async def reconcile_on_startup(
    *,
    pool: asyncpg.Pool,
    bus: NatsClient,
    adapters: dict[BotId, ExchangeClient],
    position_lifecycle_tasks: dict[int, asyncio.Task[None]],
    race_window_seconds: int,
    position_poll_interval_s: float,
    position_poll_stale_ticks: int,
    bound_logger: BoundLogger,
    now_fn: Callable[[], datetime],
) -> None:
    """T-221 startup reconciliation. One DB connection; per-bot loop in sorted bot_id order."""
    if not adapters:
        bound_logger.info("reconcile.empty_adapter_pool_no_op")
        return

    bot_ids = sorted(str(bot_id) for bot_id in adapters)
    async with pool.acquire() as conn:
        ps_rows = await select_position_states_for_bots(conn, bot_ids)

        ps_rows_by_bot: dict[str, list[PositionStateRow]] = {bot_id: [] for bot_id in bot_ids}
        for row in ps_rows:
            ps_rows_by_bot.setdefault(row.bot_id, []).append(row)

        for bot_id in bot_ids:
            adapter = adapters[bot_id]  # type: ignore[index]
            try:
                positions = await adapter.get_positions()
            except Exception as exc:
                bound_logger.error(
                    "reconcile.get_positions_failed",
                    bot_id=bot_id,
                    error=str(exc),
                )
                raise

            ex_positions: dict[str, Position] = {p.symbol: p for p in positions if p.size > 0}
            db_rows: dict[str, PositionStateRow] = {
                r.symbol: r for r in ps_rows_by_bot.get(bot_id, [])
            }

            orphan_db = [r for s, r in db_rows.items() if s not in ex_positions]
            orphan_ex = [p for s, p in ex_positions.items() if s not in db_rows]
            matching = [r for s, r in db_rows.items() if s in ex_positions]

            for row in orphan_db:
                await _close_orphan_db(
                    conn=conn,
                    bound_logger=bound_logger,
                    bot_id=bot_id,
                    row=row,
                    now_fn=now_fn,
                )

            for pos in orphan_ex:
                await _handle_orphan_exchange(
                    conn=conn,
                    adapter=adapter,
                    bound_logger=bound_logger,
                    bot_id=bot_id,
                    position=pos,
                    race_window_seconds=race_window_seconds,
                    now_fn=now_fn,
                )

            for row in matching:
                _spawn_matching_monitor(
                    bot_id=bot_id,
                    row=row,
                    pool=pool,
                    bus=bus,
                    adapter=adapter,
                    bound_logger=bound_logger,
                    position_lifecycle_tasks=position_lifecycle_tasks,
                    position_poll_interval_s=position_poll_interval_s,
                    position_poll_stale_ticks=position_poll_stale_ticks,
                    now_fn=now_fn,
                )


async def _close_orphan_db(
    *,
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
    bound_logger: BoundLogger,
    bot_id: str,
    row: PositionStateRow,
    now_fn: Callable[[], datetime],
) -> None:
    """H-020 step 3 + Path A: resolve close_order_id from open_order_id; tx-wrap update+delete."""
    open_oid = await select_open_order_id_by_trade_id(conn, row.trade_id)
    if open_oid is None:
        bound_logger.error(
            "reconcile.orphan_db_open_order_missing",
            bot_id=bot_id,
            trade_id=row.trade_id,
            symbol=row.symbol,
        )
        return
    async with conn.transaction():
        await update_trade_close(
            conn,
            trade_id=row.trade_id,
            exit_price=Decimal("0"),
            realized_pnl=Decimal("0"),
            fees_paid=None,
            closed_at=now_fn(),
            close_reason="reconcile_gone",
            close_order_id=open_oid,
        )
        await delete_position_state(conn, bot_id=bot_id, symbol=row.symbol)
    bound_logger.warning(
        "reconcile.orphan_db_closed",
        bot_id=bot_id,
        trade_id=row.trade_id,
        symbol=row.symbol,
        close_order_id=open_oid,
    )


async def _handle_orphan_exchange(
    *,
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy,
    adapter: ExchangeClient,
    bound_logger: BoundLogger,
    bot_id: str,
    position: Position,
    race_window_seconds: int,
    now_fn: Callable[[], datetime],
) -> None:
    """H-020 step 4 + H-026 race-window guard."""
    if position.side is None:
        return
    in_race = await select_recent_open_trade_exists(
        conn,
        bot_id=bot_id,
        symbol=position.symbol,
        since=now_fn() - timedelta(seconds=race_window_seconds),
    )
    if in_race:
        bound_logger.info(
            "reconcile.orphan_exchange_in_race_window",
            bot_id=bot_id,
            symbol=position.symbol,
            size=str(position.size),
            side=position.side,
        )
        return
    opposite = _OPPOSITE_SIDE[position.side]
    try:
        await adapter.place_market_order(
            symbol=position.symbol,
            side=opposite,  # type: ignore[arg-type]
            qty=position.size,
            reduce_only=True,
        )
    except Exception as exc:
        bound_logger.error(
            "reconcile.orphan_exchange_close_failed",
            bot_id=bot_id,
            symbol=position.symbol,
            size=str(position.size),
            side=position.side,
            error=str(exc),
        )
        return
    bound_logger.warning(
        "reconcile.orphan_exchange_market_closed",
        bot_id=bot_id,
        symbol=position.symbol,
        size=str(position.size),
        side=position.side,
        close_side=opposite,
    )


def _spawn_matching_monitor(
    *,
    bot_id: str,
    row: PositionStateRow,
    pool: asyncpg.Pool,
    bus: NatsClient,
    adapter: ExchangeClient,
    bound_logger: BoundLogger,
    position_lifecycle_tasks: dict[int, asyncio.Task[None]],
    position_poll_interval_s: float,
    position_poll_stale_ticks: int,
    now_fn: Callable[[], datetime],
) -> None:
    """H-020 step 5: rehydrate FSM via run_position_monitor_for_trade."""
    task = asyncio.create_task(
        run_position_monitor_for_trade(
            bot_id=bot_id,  # type: ignore[arg-type]
            symbol=row.symbol,
            trade_id=row.trade_id,
            side=row.side,
            entry_price=row.entry_price,
            qty=row.qty,
            pool=pool,
            bus=bus,
            adapter=adapter,
            bound_logger=bound_logger,
            poll_interval_s=position_poll_interval_s,
            stale_ticks_threshold=position_poll_stale_ticks,
            now_fn=now_fn,
        ),
        name=f"lifecycle_{bot_id}_{row.trade_id}",
    )
    position_lifecycle_tasks[row.trade_id] = task
    bound_logger.info(
        "reconcile.matching_resumed",
        bot_id=bot_id,
        trade_id=row.trade_id,
        symbol=row.symbol,
        side=row.side,
    )
