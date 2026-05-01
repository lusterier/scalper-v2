"""§9.5 steps 1-9 order placement pipeline (T-216a + T-216b1 + T-216b2).

Per-bot subscriptions registered at lifespan step 6 (after adapter pool
composition): one ``bus.subscribe(orders.requests.<bot_id>, handler)``
per bot in ``app.state.adapters``. Subject is bot-bound at registration;
:func:`make_per_bot_handler` is a closure factory returning the per-bot
async handler wrapped in :class:`OrderRequestDedupConsumer` (T-216b2;
H-009 ring per-bot capacity from Settings). No subject parser — each
subscription is bot-bound at registration time (path (b) per plan-reviewer
Risk #1 resolution).

Per-message handler (single :func:`make_per_bot_handler` body):

1. Validate envelope payload as :class:`OrderRequest`; on failure → log
   ERROR + counter, drop (programmer-error fail-loud per OQ-7).
2. Assert ``request.bot_id == closure_bound_bot_id``; on mismatch → log
   WARN ``execution.subject_payload_botid_mismatch_using_subject`` and
   continue using subject bot_id as authoritative (CONCERN #6).
3. Step-size TODO: log WARN ``execution.qty_step_rounding_pending_t_f2_plus``
   pre-place call; T-F2+ instruments-info cache replaces this with
   deterministic per-symbol qty rounding (BLOCKER #3 — re-promoted in
   ``chore(tasks)`` commit ``91b6fd3``).
4. Call ``adapter.set_leverage(symbol, leverage)`` (LRU cache adapter-internal).
5. Call ``adapter.place_market_order(symbol, side, qty)``; on
   :class:`UnknownState` raised → log ERROR + counter (NO DLQ, NO retry,
   NO duplicate per H-003; T-221 reconciliation owns recovery). On
   :class:`OrderRejected` with reason containing "precision"/"qty" →
   log key ``execution.place_market_order_qty_rejected``
   (operator-actionable signal until T-F2+ step-size cache lands).
6. Call ``adapter.get_fill_price(symbol, exchange_order_id)`` with inline
   retry per Settings ``execution_fill_price_retry_*`` (CONCERN #7 / L-001);
   if None after all attempts → DLQ + raise :class:`FillPriceUnresolvedError`.
7. **T-216b2 — paper branch fork**: if ``request.exchange_mode == "paper"``,
   call ``set_trading_stop(Full, sl)`` + ``set_trading_stop(Partial, tp, tp_size)``
   then return (PaperExchange persists paper_* internally; T-218 emits
   ``OrderFilled`` from ``stream_executions``).
8. **T-216b2 — live/testnet SL set** (§9.5 step 6, H-013 explicit ``Full``):
   on exception (AuthError / OrderRejected / NetworkTimeout / RateLimitError /
   UnknownState) → invoke :func:`emergency_close` (T-216b1 H-004 path) and return.
9. **T-216b2 — live/testnet TP set** (§9.5 step 7, H-013 explicit ``Partial``):
   on exception → log ERROR ``execution.tp_set_failed_continuing_with_sl_only``
   and continue (OQ-2 default A; position remains with SL only; T-217 monitor takes over).
10. **T-216b2 — persistence-tx** (§9.5 step 8): single tx via
    :func:`persist_placement_tx` (5 INSERTs: orders + trades + position_state
    + 2 trading_events). On failure → log ERROR + return (orphan; T-221 reconciles).
11. **T-216b2 — post-commit emit** (§9.5 step 9): :func:`emit_post_commit_events`
    publishes ``OrderPlaced`` + ``SLMoved`` to ``orders.events.<bot_id>`` per
    Q2 publish-after-persist contract (OUTSIDE tx).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from packages.bus.schemas.orders import OrderRequest, subject_for_orders_dlq
from packages.exchange.errors import (
    AuthError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
    UnknownState,
)

from .placement_persist import (
    OrderRequestDedupConsumer,
    compute_notional_usd,
    compute_sl_price,
    compute_tp_price,
    compute_tp_size,
    emergency_close,
    emit_post_commit_events,
    persist_placement_tx,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import MessageEnvelope, NatsClient
    from packages.core import BotId
    from packages.exchange.protocols import ExchangeClient


__all__ = ["FillPriceUnresolvedError", "make_per_bot_handler"]


class FillPriceUnresolvedError(RuntimeError):
    """Adapter returned None for ``get_fill_price`` after configured max attempts.

    Lives in ``services/execution/app/placement.py``, NOT
    ``packages/exchange/errors.py`` (CONCERN #8): represents an
    execution-service orchestration outcome (Nx inline retry exhausted),
    not an adapter call failure. §11.3 error taxonomy in
    :mod:`packages.exchange.errors` covers adapter→caller error contracts
    only; upper-layer orchestration errors stay local to the consuming service.
    """


def make_per_bot_handler(
    *,
    bot_id: BotId,
    adapter: ExchangeClient,
    bus: NatsClient,
    logger: BoundLogger,
    pool: asyncpg.Pool,
    dedup_capacity: int,
    now_fn: Callable[[], datetime],
    fill_price_retry_attempts: int,
    fill_price_retry_backoff_s: float,
) -> Callable[[MessageEnvelope], Awaitable[None]]:
    """Closure factory returning the per-bot ``orders.requests.<bot_id>`` handler.

    Bot identity + adapter + pool + Settings knobs are bound in the closure
    at registration time. The inner ``_handle`` coroutine is wrapped in
    :class:`OrderRequestDedupConsumer` (H-009 per-bot ring; capacity from
    Settings); the returned callable is :meth:`OrderRequestDedupConsumer.consume`
    so duplicate ``(bot_id, signal_id)`` envelopes are filtered before the
    handler runs. Match :data:`packages.bus.client.Handler` shape.

    ``now_fn`` injected per §N1 UTC; default at the lifespan call site is
    ``lambda: datetime.now(UTC)``. Threaded through to :func:`emergency_close`
    and :func:`persist_placement_tx` for ``sl_set_at`` / close timestamps.
    """

    async def _handle(envelope: MessageEnvelope) -> None:
        try:
            request = OrderRequest.model_validate(envelope.payload)
        except Exception as exc:
            logger.error(
                "execution.order_request_validation_failed",
                bot_id=bot_id,
                error=str(exc),
            )
            return
        if request.bot_id != bot_id:
            logger.warning(
                "execution.subject_payload_botid_mismatch_using_subject",
                subject_bot_id=bot_id,
                payload_bot_id=request.bot_id,
            )
        # BLOCKER #3 — pre-place qty-step rounding TODO (operator visibility).
        logger.warning(
            "execution.qty_step_rounding_pending_t_f2_plus",
            bot_id=bot_id,
            symbol=request.symbol,
            qty=str(request.qty),
        )
        # 4. set_leverage (LRU cached adapter-internal).
        try:
            await adapter.set_leverage(request.symbol, request.leverage)
        except (AuthError, OrderRejected, NetworkTimeout, RateLimitError) as exc:
            logger.error(
                "execution.set_leverage_failed",
                bot_id=bot_id,
                symbol=request.symbol,
                error=str(exc),
            )
            return
        # 5. place_market_order (H-003 zero-retry; UnknownState catch path).
        try:
            place_result = await adapter.place_market_order(
                request.symbol,
                request.side,
                request.qty,
            )
        except UnknownState as exc:
            logger.error(
                "execution.place_market_order_unknown_state",
                bot_id=bot_id,
                symbol=request.symbol,
                error=str(exc),
            )
            return
        except OrderRejected as exc:
            reason_lower = (exc.reason or "").lower()
            if "precision" in reason_lower or "qty" in reason_lower:
                log_key = "execution.place_market_order_qty_rejected"
            else:
                log_key = "execution.place_market_order_rejected"
            logger.error(
                log_key,
                bot_id=bot_id,
                symbol=request.symbol,
                reason=exc.reason,
            )
            return
        except (AuthError, NetworkTimeout, RateLimitError) as exc:
            logger.error(
                "execution.place_market_order_failed",
                bot_id=bot_id,
                symbol=request.symbol,
                error=str(exc),
            )
            return
        # 6. get_fill_price with inline retry (Settings-tunable per L-001).
        fill_price: Decimal | None = None
        for attempt in range(fill_price_retry_attempts):
            fill_price = await adapter.get_fill_price(
                request.symbol,
                place_result.exchange_order_id,
            )
            if fill_price is not None:
                break
            if attempt + 1 < fill_price_retry_attempts:
                await asyncio.sleep(fill_price_retry_backoff_s)
        if fill_price is None:
            logger.error(
                "execution.fill_price_unresolved",
                bot_id=bot_id,
                exchange_order_id=place_result.exchange_order_id,
            )
            try:
                await bus.publish(subject_for_orders_dlq(bot_id), envelope)
            except Exception as exc:
                logger.error(
                    "execution.dlq_publish_failed",
                    bot_id=bot_id,
                    error=str(exc),
                )
            raise FillPriceUnresolvedError(
                f"fill_price None after {fill_price_retry_attempts} attempts"
            )
        # T-216b1+T-216b2 — post-fill_price pipeline (§9.5 steps 6-9).
        sl_price = compute_sl_price(request.side, fill_price, request.sl_pct)
        tp_price = compute_tp_price(request.side, fill_price, request.tp_pct)
        tp_size = compute_tp_size(request.qty, request.tp_qty_pct)
        notional_usd = compute_notional_usd(request.qty, fill_price)

        # 7. Paper-bot fork — PaperExchange persists paper_* internally;
        # T-218 emits OrderFilled from stream_executions. Skip persistence + emit.
        if request.exchange_mode == "paper":
            await adapter.set_trading_stop(
                request.symbol,
                tpsl_mode="Full",
                sl_price=sl_price,
            )
            await adapter.set_trading_stop(
                request.symbol,
                tpsl_mode="Partial",
                tp_price=tp_price,
                tp_size=tp_size,
            )
            return

        # 8. SL set (§9.5 step 6, H-013 explicit Full). On any failure:
        # emergency_close (T-216b1 H-004 path).
        try:
            await adapter.set_trading_stop(
                request.symbol,
                tpsl_mode="Full",
                sl_price=sl_price,
            )
        except (AuthError, OrderRejected, NetworkTimeout, RateLimitError, UnknownState) as exc:
            logger.error(
                "execution.set_trading_stop_sl_failed_invoking_emergency_close",
                bot_id=bot_id,
                symbol=request.symbol,
                error=str(exc),
            )
            await emergency_close(
                adapter=adapter,
                bus=bus,
                pool=pool,
                bound_logger=logger,
                bot_id=bot_id,
                request=request,
                envelope=envelope,
                place_result=place_result,
                fill_price=fill_price,
                now_fn=now_fn,
            )
            return
        sl_set_at = now_fn()

        # 9. TP set (§9.5 step 7, H-013 explicit Partial). On failure:
        # log + continue (OQ-2 default A — position open with SL only;
        # T-217 monitor takes over).
        try:
            await adapter.set_trading_stop(
                request.symbol,
                tpsl_mode="Partial",
                tp_price=tp_price,
                tp_size=tp_size,
            )
        except (AuthError, OrderRejected, NetworkTimeout, RateLimitError, UnknownState) as exc:
            logger.error(
                "execution.tp_set_failed_continuing_with_sl_only",
                bot_id=bot_id,
                symbol=request.symbol,
                error=str(exc),
            )

        # 10. Persistence-tx (§9.5 step 8). On failure: orphan; T-221 reconciles.
        try:
            async with pool.acquire() as conn, conn.transaction():
                order_placed_payload, sl_moved_payload = await persist_placement_tx(
                    conn=conn,
                    bot_id=bot_id,
                    request=request,
                    envelope=envelope,
                    place_result=place_result,
                    fill_price=fill_price,
                    sl_price=sl_price,
                    tp_price=tp_price,
                    tp_size=tp_size,
                    notional_usd=notional_usd,
                    sl_set_at=sl_set_at,
                )
        except Exception as exc:
            logger.error(
                "execution.placement_persist_tx_failed",
                bot_id=bot_id,
                symbol=request.symbol,
                error=str(exc),
            )
            return

        # 11. Post-commit emit (§9.5 step 9, OUTSIDE tx — Q2 publish-after-persist).
        await emit_post_commit_events(
            bus=bus,
            bot_id=bot_id,
            correlation_id=envelope.correlation_id,
            order_placed_payload=order_placed_payload,
            sl_moved_payload=sl_moved_payload,
            bound_logger=logger,
        )

    consumer = OrderRequestDedupConsumer(
        handler=_handle,
        capacity=dedup_capacity,
        bound_logger=logger,
    )
    return consumer.consume
