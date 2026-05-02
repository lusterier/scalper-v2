"""§9.5 line 1591 ExecutionEvent dispatcher (T-218a + T-218b).

Per-bot dispatcher consuming :meth:`ExchangeClient.stream_executions`.
Wraps T-210 :class:`packages.bus.dedup.DedupingConsumer` keyed on
``event.exchange_exec_id`` (H-009 ring; capacity from Settings).
T-218a ships the class skeleton + lifespan task wiring; T-218b owns
the :meth:`ExecutionDispatcher._process` body (orders lookup,
exec_type derivation, INSERT execution, UPDATE position_state, UPDATE
trade fees, T-219 close forward-pointer).

The ``run_dispatcher_for_bot`` task pumps the adapter's stream into
the dispatcher's :meth:`DedupingConsumer.consume` so duplicate-keyed
events are dropped before the body runs.

Lifespan ordering (per main.py reverse-shutdown contract):

1. ``bus.close()`` — drains placement subscriptions.
2. ``dispatcher_tasks`` cancel — dispatchers consume from
   ``adapter.stream_executions()``; cancelling them before the
   adapter prevents mid-iter raises (graceful stop).
3. ``ws_tasks`` + ``paper_consumer_tasks`` cancel.
4. ``adapter.close()`` per bot.
5. ``pool.close()``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from packages.bus.dedup import DedupingConsumer
from packages.db.queries.execution import (
    insert_execution,
    select_open_order_id_by_trade_id,
    select_order_id_by_exchange_id,
    select_position_state,
    select_trade_by_close_order_id,
    select_trade_by_open_order_id,
    update_position_state_after_fill,
    update_trade_fees_incremental,
)

from .reconcile import emit_post_commit_close_event, reconcile_close

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient
    from packages.bus.schemas.orders import OrderClosed
    from packages.core import BotId, CorrelationId
    from packages.exchange.protocols import ExchangeClient
    from packages.exchange.types import ExecutionEvent

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = ["ExecutionDispatcher", "run_dispatcher_for_bot"]


class ExecutionDispatcher(DedupingConsumer["ExecutionEvent"]):
    """§20 H-009 per-bot dedup ring keyed on :attr:`ExecutionEvent.exchange_exec_id`.

    Subclass of :class:`DedupingConsumer`. T-218a ships the ctor +
    skeleton; T-218b overrides :meth:`_process` body.

    Ctor DI per §N6: bot_id / pool / bus / bound_logger / capacity / now_fn.
    No module-level mutable state. ``now_fn`` injected for testable
    UTC timestamps (per §N1) — production lifespan wires
    ``lambda: datetime.now(UTC)``.

    Capacity comes from ``Settings.dispatch_dedup_capacity`` (default
    10000 per §9.5:1591 "ring buffer, size 10k"; configurable per §N9).
    """

    def __init__(
        self,
        *,
        bot_id: BotId,
        pool: asyncpg.Pool,
        bus: NatsClient,
        bound_logger: BoundLogger,
        capacity: int,
        now_fn: Callable[[], datetime],
        adapter: ExchangeClient,
        sub_account: str,
        closed_pnl_lock: asyncio.Lock,
        closed_pnl_post_close_sleep_s: float,
    ) -> None:
        super().__init__(
            key_fn=lambda event: event.exchange_exec_id,
            capacity=capacity,
            logger=bound_logger,
        )
        self._bot_id = bot_id
        self._pool = pool
        self._bus = bus
        self._bound_logger = bound_logger
        self._now_fn = now_fn
        self._adapter = adapter
        self._sub_account = sub_account
        self._closed_pnl_lock = closed_pnl_lock
        self._closed_pnl_post_close_sleep_s = closed_pnl_post_close_sleep_s

    @property
    def bot_id(self) -> BotId:
        """Public read-only access to bound bot_id (used by run_dispatcher_for_bot logging)."""
        return self._bot_id

    async def _process(self, message: ExecutionEvent) -> None:
        """§9.5:1591 + H-024 (v2 per ADR-0005). See ``docs/plans/T-218b.md`` for full design.

        Single tx wraps lookups + INSERT + UPDATEs + (optional) close forward-pointer.
        Defensive halts (RuntimeError + tx rollback): unattributable / orphan_order_match
        / orphan_synthetic_fill / over-fill (preserves §9.5:1613 invariant).

        T-219 close-flow: when ``ps_after.remaining_qty == 0``, ``reconcile_close``
        returns ``(OrderClosed_payload, correlation_id, _exch_id)`` captured INSIDE
        the tx; AFTER tx commits, ``emit_post_commit_close_event`` publishes the
        event wrapped in :class:`MessageEnvelope` per Q2 publish-after-persist.
        """
        close_event_payload: OrderClosed | None = None
        close_event_correlation_id: CorrelationId | None = None
        async with self._pool.acquire() as conn, conn.transaction():
            order_id_match = await select_order_id_by_exchange_id(
                conn,
                message.exchange_order_id,
            )

            exec_type, trade_id, _sl_type_observed = await _derive_exec_type(
                conn=conn,
                bot_id=self._bot_id,
                event=message,
                order_id_match=order_id_match,
                bound_logger=self._bound_logger,
            )

            if exec_type == "unknown" and trade_id is not None:
                ps_check = await select_position_state(
                    conn,
                    bot_id=self._bot_id,
                    symbol=message.symbol,
                )
                if ps_check is not None and message.qty > ps_check.remaining_qty:
                    self._bound_logger.error(
                        "execution.dispatcher_overfill_halt",
                        bot_id=self._bot_id,
                        exchange_exec_id=message.exchange_exec_id,
                        event_qty=str(message.qty),
                        remaining_qty=str(ps_check.remaining_qty),
                    )
                    msg = "over-fill: event.qty > position_state.remaining_qty"
                    raise RuntimeError(msg)

            if order_id_match is not None and trade_id is None:
                self._bound_logger.error(
                    "execution.dispatcher_orphan_order_halt",
                    bot_id=self._bot_id,
                    exchange_order_id=message.exchange_order_id,
                    exchange_exec_id=message.exchange_exec_id,
                    order_id_match=order_id_match,
                )
                msg = (
                    f"orphan order match: orders.id={order_id_match} matched but "
                    f"no trade references it as open or close"
                )
                raise RuntimeError(msg)

            if order_id_match is not None:
                order_id_for_insert = order_id_match
            elif trade_id is not None:
                resolved = await select_open_order_id_by_trade_id(conn, trade_id)
                if resolved is None:
                    self._bound_logger.error(
                        "execution.dispatcher_orphan_synthetic_fill",
                        bot_id=self._bot_id,
                        trade_id=trade_id,
                        exchange_exec_id=message.exchange_exec_id,
                    )
                    msg = f"orphan synthetic fill: trade_id={trade_id} not found"
                    raise RuntimeError(msg)
                order_id_for_insert = resolved
            else:
                self._bound_logger.error(
                    "execution.dispatcher_unattributable_fill",
                    bot_id=self._bot_id,
                    exchange_exec_id=message.exchange_exec_id,
                    exchange_order_id=message.exchange_order_id,
                )
                msg = "unattributable fill: no order match and no position_state"
                raise RuntimeError(msg)

            await insert_execution(
                conn,
                exchange_exec_id=message.exchange_exec_id,
                order_id=order_id_for_insert,
                trade_id=trade_id,
                bot_id=self._bot_id,
                symbol=message.symbol,
                side=message.side,
                price=message.price,
                qty=message.qty,
                fee=message.fee,
                exec_type=exec_type,
                executed_at=message.executed_at,
            )

            new_sl_type: Literal["protective", "be", "trail"] | None = (
                "trail" if exec_type == "partial_tp" else None
            )
            await update_position_state_after_fill(
                conn,
                bot_id=self._bot_id,
                symbol=message.symbol,
                qty_delta=message.qty,
                new_sl_type=new_sl_type,
                updated_at=self._now_fn(),
            )

            if trade_id is not None:
                await update_trade_fees_incremental(
                    conn,
                    trade_id=trade_id,
                    fee_delta=message.fee,
                )

            ps_after = await select_position_state(
                conn,
                bot_id=self._bot_id,
                symbol=message.symbol,
            )
            if ps_after is not None and ps_after.remaining_qty == Decimal("0"):
                if trade_id is None:
                    msg = "internal invariant: trade_id None at close-trigger block"
                    raise RuntimeError(msg)
                payload, corr_id, _exch_id = await reconcile_close(
                    conn=conn,
                    adapter=self._adapter,
                    bound_logger=self._bound_logger,
                    bot_id=self._bot_id,
                    symbol=message.symbol,
                    sub_account=self._sub_account,
                    closed_pnl_lock=self._closed_pnl_lock,
                    closed_pnl_post_close_sleep_s=self._closed_pnl_post_close_sleep_s,
                    trade_id=trade_id,
                    close_order_id=order_id_match,
                    exec_type=exec_type,
                    fees_paid_at_close=None,
                    final_fill_price=message.price,
                    closed_at=message.executed_at,
                )
                close_event_payload = payload
                close_event_correlation_id = corr_id

        # AFTER tx commits — emit OrderClosed wrapped in MessageEnvelope per Q2
        # publish-after-persist (T-216b2 vzor; mirror placement.py:324-331).
        if close_event_payload is not None and close_event_correlation_id is not None:
            await emit_post_commit_close_event(
                bus=self._bus,
                bot_id=self._bot_id,
                correlation_id=close_event_correlation_id,
                order_closed_payload=close_event_payload,
                bound_logger=self._bound_logger,
            )


async def run_dispatcher_for_bot(
    *,
    adapter: ExchangeClient,
    dispatcher: ExecutionDispatcher,
    bound_logger: BoundLogger,
) -> None:
    """Background task body — pump :meth:`ExchangeClient.stream_executions`
    into :meth:`ExecutionDispatcher.consume`.

    Lifecycle:

    * Normal flow: ``async for event in adapter.stream_executions()``
      delivers each event to dedup ring → ``_process`` body.
    * Cancellation (lifespan reverse-shutdown): :class:`asyncio.CancelledError`
      propagated cleanly without log emit (graceful stop signal).
    * Mid-flight stream failure (per-bot isolation; do NOT crash service):
      log ERROR ``execution.dispatcher_stream_terminated`` + re-raise.
      Lifespan gathers with ``return_exceptions=True`` so the failed
      task is reported but service stays up. Diverges from T-216a WG#7
      fail-fast (startup) — per-bot isolation > fail-fast for mid-flight.
      T-221 post-restart reconciliation is the recovery path.
    """
    try:
        async for event in adapter.stream_executions():
            await dispatcher.consume(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        bound_logger.error(
            "execution.dispatcher_stream_terminated",
            bot_id=dispatcher.bot_id,
            error=str(exc),
        )
        raise


async def _derive_exec_type(
    *,
    conn: _DbExecutor,
    bot_id: BotId,
    event: ExecutionEvent,
    order_id_match: int | None,
    bound_logger: BoundLogger,
) -> tuple[str, int | None, str | None]:
    """Return ``(exec_type, trade_id, sl_type_observed)`` per H-024 v2 (ADR-0005).

    Pure derivation; no writes. Module-private — testable in isolation.
    Full branch table in ``docs/plans/T-218b.md`` § ``_derive_exec_type``.
    """
    if order_id_match is not None:
        trade_open = await select_trade_by_open_order_id(conn, order_id_match)
        if trade_open is not None:
            return ("open", trade_open.id, None)
        trade_close = await select_trade_by_close_order_id(conn, order_id_match)
        if trade_close is not None:
            return ("close", trade_close.id, None)
        bound_logger.warning(
            "execution.dispatcher_orphan_order_match",
            bot_id=bot_id,
            exchange_order_id=event.exchange_order_id,
            exchange_exec_id=event.exchange_exec_id,
            order_id_match=order_id_match,
        )
        return ("unknown", None, None)

    ps = await select_position_state(conn, bot_id=bot_id, symbol=event.symbol)
    if ps is None:
        bound_logger.warning(
            "execution.dispatcher_exec_type_unknown",
            bot_id=bot_id,
            reason="no_position_state",
            exchange_exec_id=event.exchange_exec_id,
        )
        return ("unknown", None, None)

    if event.side == ps.side:
        bound_logger.warning(
            "execution.dispatcher_exec_type_unknown",
            bot_id=bot_id,
            reason="same_side_synthetic_fill",
            exchange_exec_id=event.exchange_exec_id,
        )
        return ("unknown", ps.trade_id, ps.sl_type)

    if event.qty < ps.remaining_qty:
        return ("partial_tp", ps.trade_id, "trail")

    if event.qty == ps.remaining_qty:
        if ps.sl_type == "trail":
            return ("trail", ps.trade_id, ps.sl_type)
        if ps.sl_type in ("protective", "be"):
            return ("sl", ps.trade_id, ps.sl_type)
        bound_logger.warning(
            "execution.dispatcher_exec_type_unknown",
            bot_id=bot_id,
            reason=f"sl_type_unrecognized:{ps.sl_type!r}",
            exchange_exec_id=event.exchange_exec_id,
        )
        return ("unknown", ps.trade_id, ps.sl_type)

    bound_logger.warning(
        "execution.dispatcher_exec_type_unknown",
        bot_id=bot_id,
        reason="qty_exceeds_remaining",
        exchange_exec_id=event.exchange_exec_id,
        event_qty=str(event.qty),
        remaining_qty=str(ps.remaining_qty),
    )
    return ("unknown", ps.trade_id, ps.sl_type)
