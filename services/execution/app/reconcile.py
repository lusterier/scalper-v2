"""§9.5:1592 + §9.5:1594-1599 close-flow reconciliation (T-219 owner per ADR-0006).

Cumulative-delta strategy per ADR-0006 D1-D5 + D6 hazard-test mapping:

* **D1** snapshot pair triggers ONLY on full-close (caller's close-trigger boundary).
* **D2** single sleep before the AFTER snapshot via
  ``Settings.execution_closed_pnl_post_close_sleep_s`` (default 2.0; H-011).
* **D3** full delta to single trade (single-trade single-call attribution).
* **D4** per-sub-account ``asyncio.Lock`` — narrow scope around BEFORE→sleep→AFTER
  triplet only; per-trade UPDATE/DELETE/emit are PK-independent and don't need
  cross-bot serialization.
* **D5** atomic close persistence inside caller's tx + post-commit emit
  (mirror T-216b2 ``persist_placement_tx`` + ``emit_post_commit_events`` split
  at ``services/execution/app/placement_persist.py:466-500``).

T-218b dispatcher's ``_process`` body invokes :func:`reconcile_close` inside its
``conn.transaction()`` block, captures the returned ``(OrderClosed, correlation_id,
exchange_order_id)`` tuple, exits the tx (commit), then invokes
:func:`emit_post_commit_close_event` post-commit per Q2 publish-after-persist.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from packages.bus.schemas.orders import OrderClosed, subject_for_orders_event
from packages.db.queries.execution import (
    delete_position_state,
    select_open_order_id_by_trade_id,
    select_order_meta_by_id,
    update_trade_close,
)

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy
    from structlog.stdlib import BoundLogger

    from packages.bus import BusProtocol
    from packages.core import CorrelationId
    from packages.exchange.protocols import ExchangeClient

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = ["emit_post_commit_close_event", "reconcile_close"]


_EXEC_TYPE_TO_CLOSE_REASON = {
    "close": "manual",
    "sl": "sl",
    "trail": "trail",
    "unknown": "unknown",
}


async def reconcile_close(
    *,
    conn: _DbExecutor,
    adapter: ExchangeClient,
    bound_logger: BoundLogger,
    bot_id: str,
    symbol: str,
    sub_account: str,
    closed_pnl_lock: asyncio.Lock,
    closed_pnl_post_close_sleep_s: float,
    trade_id: int,
    close_order_id: int | None,
    exec_type: str,
    fees_paid_at_close: Decimal | None,
    final_fill_price: Decimal,
    closed_at: datetime,
) -> tuple[OrderClosed, CorrelationId, str]:
    """T-219 cumulative-delta close-flow PERSIST body per ADR-0006 D1-D5.

    Returns ``(OrderClosed_payload, correlation_id, exchange_order_id)`` for
    caller-side post-commit emit per Q2 publish-after-persist contract.
    Caller (T-218b dispatcher ``_process``) captures the return tuple INSIDE
    the tx, exits the tx (commit), then invokes
    :func:`emit_post_commit_close_event` post-commit.

    Synthetic close (no orders.id match) resolves ``close_order_id`` via
    :func:`select_open_order_id_by_trade_id` per ADR-0006 D5 amendment;
    OPEN order's ``exchange_order_id`` is then read via
    :func:`select_order_meta_by_id` for the ``OrderClosed`` envelope
    (no empty-string workaround per Gate-1 CONCERN #4 fix).
    """
    if close_order_id is None:
        resolved = await select_open_order_id_by_trade_id(conn, trade_id)
        if resolved is None:
            bound_logger.error(
                "execution.reconcile_close_orphan_trade",
                bot_id=bot_id,
                trade_id=trade_id,
                symbol=symbol,
            )
            msg = f"orphan trade: trade_id={trade_id} not found in trades table"
            raise RuntimeError(msg)
        close_order_id = resolved

    order_meta = await select_order_meta_by_id(conn, close_order_id)
    if order_meta is None:
        bound_logger.error(
            "execution.reconcile_close_order_meta_missing",
            bot_id=bot_id,
            trade_id=trade_id,
            close_order_id=close_order_id,
        )
        msg = f"orders row id={close_order_id} not found"
        raise RuntimeError(msg)
    correlation_id_str, close_exchange_order_id = order_meta
    from packages.core import CorrelationId

    correlation_id = CorrelationId(correlation_id_str)

    # D4 — narrow Lock scope: BEFORE→sleep→AFTER triplet only.
    async with closed_pnl_lock:
        before_total = await adapter.get_closed_pnl_cumulative(sub_account)
        # D2 — single sleep before AFTER snapshot only (H-011).
        await asyncio.sleep(closed_pnl_post_close_sleep_s)
        after_total = await adapter.get_closed_pnl_cumulative(sub_account)

    # D3 — full delta to single trade.
    delta = after_total - before_total

    close_reason = _EXEC_TYPE_TO_CLOSE_REASON.get(exec_type, "unknown")

    await update_trade_close(
        conn,
        trade_id=trade_id,
        exit_price=final_fill_price,
        realized_pnl=delta,
        fees_paid=fees_paid_at_close,
        closed_at=closed_at,
        close_reason=close_reason,
        close_order_id=close_order_id,
    )
    await delete_position_state(conn, bot_id=bot_id, symbol=symbol)

    bound_logger.info(
        "execution.reconcile_close_persisted",
        bot_id=bot_id,
        trade_id=trade_id,
        symbol=symbol,
        realized_pnl=str(delta),
        close_reason=close_reason,
    )

    order_closed_payload = OrderClosed(
        bot_id=bot_id,
        order_id=close_order_id,
        exchange_order_id=close_exchange_order_id,
        symbol=symbol,
        timestamp=closed_at,
        realized_pnl=delta,
        close_reason=close_reason,
    )
    return order_closed_payload, correlation_id, close_exchange_order_id


async def emit_post_commit_close_event(
    *,
    bus: BusProtocol,
    bot_id: str,
    correlation_id: CorrelationId,
    order_closed_payload: OrderClosed,
    bound_logger: BoundLogger,
) -> None:
    """Post-commit publisher for ``OrderClosed`` (mirror T-216b2 ``emit_post_commit_events``).

    Wraps payload in :class:`MessageEnvelope` with correlation_id + publisher
    string per audit-grade event contract (§N2 / §8.4). Best-effort: publish
    failure logged but does NOT raise — DB tx already committed; T-220 audit
    catches missing event via DB-vs-NATS divergence.
    """
    from packages.bus import MessageEnvelope as _Env

    try:
        envelope = _Env(
            correlation_id=correlation_id,
            publisher="execution-service",
            payload=order_closed_payload.model_dump(mode="json"),
        )
        await bus.publish(subject_for_orders_event(bot_id), envelope)
    except Exception as exc:
        bound_logger.error(
            "execution.event_publish_failed",
            bot_id=bot_id,
            event_type="order_closed",
            error=str(exc),
        )
