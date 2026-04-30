"""§9.5 steps 1-5 order placement pipeline (T-216a).

Per-bot subscriptions registered at lifespan step 6 (after adapter pool
composition): one ``bus.subscribe(orders.requests.<bot_id>, handler)``
per bot in ``app.state.adapters``. Subject is bot-bound at registration;
:func:`make_per_bot_handler` is a closure factory returning the per-bot
async handler. No subject parser — each subscription is bot-bound at
registration time (path (b) per plan-reviewer Risk #1 resolution).

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
7. Raise ``NotImplementedError("T-216b: SL+TP+persist+events")`` —
   forward-pointer for T-216b owner; mirror T-208a stub-pin precedent.
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

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from decimal import Decimal

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
    fill_price_retry_attempts: int,
    fill_price_retry_backoff_s: float,
) -> Callable[[MessageEnvelope], Awaitable[None]]:
    """Closure factory returning the per-bot ``orders.requests.<bot_id>`` handler.

    Bot identity + adapter + Settings retry knobs are bound in the closure
    at registration time. Returned coroutine matches
    :data:`packages.bus.client.Handler` shape.
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
        # 7. T-216a stops here. T-216b owns SL+TP+persist+events.
        raise NotImplementedError(
            "T-216b: SL+TP+persist+events — placement pipeline post-fill_price"
        )

    return _handle
