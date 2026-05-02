"""§9.5:1592 + §9.5:1594-1599 close-flow reconciliation (T-219 owner).

T-218b ships the stub surface so :class:`ExecutionDispatcher._process`
can forward-pointer to it on ``remaining_qty == 0`` post-fill events.
T-219 replaces the ``NotImplementedError`` with the cumulative-delta
flow per §9.5:1594-1599 (snapshot ``closed_pnl_total`` before/after,
delta = realized P&L, apportion to trades in close order).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy
    from structlog.stdlib import BoundLogger

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = ["reconcile_close"]


async def reconcile_close(
    *,
    conn: _DbExecutor,
    bound_logger: BoundLogger,
    bot_id: str,
    symbol: str,
    trade_id: int,
    close_order_id: int | None,
    final_fill_price: Decimal,
    final_fill_qty: Decimal,
    final_fill_fee: Decimal,
    closed_at: datetime,
) -> None:
    """T-219 owner — cumulative-delta P&L close flow per §9.5:1594-1599.

    T-219 will add ``adapter: ExchangeClient`` parameter when implementing
    ``closed_pnl_total`` snapshot. T-218b leaves it out per Gate-1 plan-reviewer
    recommendation (B3-(a) YAGNI) — adapter is a T-219 concern, not T-218b.

    Snapshot ``closed_pnl_total`` BEFORE close (via ``adapter.get_closed_pnl_cumulative``).
    Snapshot AFTER (with H-011 configurable post-close sleep). Delta = realized P&L.
    Apportion to trades in close order (handles partial-TP multi-row + concurrent
    closes per sub-account).

    Caller (T-218b dispatcher) wraps in ``conn.transaction()``; this function
    invokes ``update_trade_close`` + ``delete_position_state`` within the
    caller's tx. NotImplementedError until T-219 lands.
    """
    raise NotImplementedError(
        "T-219: cumulative-delta close flow + update_trade_close + delete_position_state"
    )
