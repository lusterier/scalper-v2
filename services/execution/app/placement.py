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
   transient adapter exceptions (AuthError / NetworkTimeout / RateLimitError)
   are treated as None per T-216c / H-032 — warn-log + retry counter advances
   + sleep on remaining attempts; if None after all attempts → DLQ +
   raise :class:`FillPriceUnresolvedError`.
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
    QtyValidationError,
    RateLimitError,
    UnknownState,
)
from packages.exchange.quantize import quantize_qty
from packages.scoring.types import SizingTier
from packages.sizing import compute_qty_from_sizing

from .lifecycle import run_position_monitor_for_trade
from .placement_persist import (
    OrderRequestDedupConsumer,
    compute_notional_usd,
    compute_sl_price,
    compute_tp_price,
    compute_tp_size,
    emergency_close,
    emit_post_commit_events,
    emit_post_commit_shadow_start_event,
    persist_placement_tx,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import BusProtocol, MessageEnvelope
    from packages.core import BotId
    from packages.exchange.protocols import ExchangeClient

    from .metrics import Metrics


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
    sub_account: str,
    metrics: Metrics,
    adapter: ExchangeClient,
    bus: BusProtocol,
    logger: BoundLogger,
    pool: asyncpg.Pool,
    dedup_capacity: int,
    now_fn: Callable[[], datetime],
    fill_price_retry_attempts: int,
    fill_price_retry_backoff_s: float,
    position_lifecycle_tasks: dict[int, asyncio.Task[None]],
    position_poll_interval_s: float,
    position_poll_stale_ticks: int,
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
        # T-527b2b / ADR-0013: §B.1 tier sizing compute seam (before the
        # T-529 quantize). request.sizing is None → static request.qty path
        # byte-unchanged (backward-compat). On a fetch RAISE (B1) / compute
        # ValueError / sub-lowest-tier (compute None, OQ-4=A) → skip BEFORE
        # place_market_order (log + signals_skipped_sizing counter; no order,
        # no DLQ — sizing fetch is pre-trade, transient blip → defer signal;
        # mirrors the get_instrument_info transient-failure posture below).
        working_qty = request.qty
        if request.sizing is not None:
            try:
                balance = await adapter.get_account_balance(sub_account)
                mark_price = await adapter.get_mark_price(request.symbol)
            except (AuthError, NetworkTimeout, RateLimitError) as exc:
                logger.error(
                    "execution.sizing_fetch_failed",
                    bot_id=bot_id,
                    symbol=request.symbol,
                    error=str(exc),
                )
                metrics.signals_skipped_sizing.labels(
                    bot_id=str(bot_id), reason="fetch_failed"
                ).inc()
                return
            tiers = [
                SizingTier(balance_min=t.balance_min, size=t.size) for t in request.sizing.tiers
            ]
            try:
                computed_qty = compute_qty_from_sizing(
                    total_equity=balance.total_equity,
                    mark_price=mark_price,
                    tiers=tiers,
                    score=request.score,
                    score_multipliers=request.sizing.score_multipliers,
                    max_notional_per_symbol=request.sizing.max_notional_per_symbol,
                    symbol=request.symbol,
                )
            except ValueError as exc:
                logger.error(
                    "execution.sizing_compute_failed",
                    bot_id=bot_id,
                    symbol=request.symbol,
                    error=str(exc),
                )
                metrics.signals_skipped_sizing.labels(
                    bot_id=str(bot_id), reason="compute_error"
                ).inc()
                return
            if computed_qty is None:
                logger.info(
                    "execution.signal_skipped_sub_lowest_tier",
                    bot_id=bot_id,
                    symbol=request.symbol,
                    total_equity=str(balance.total_equity),
                )
                metrics.signals_skipped_sizing.labels(
                    bot_id=str(bot_id), reason="sub_lowest_tier"
                ).inc()
                return
            working_qty = computed_qty
        # 3a. T-529 / H-036: pre-flight qty quantization + validation.
        # Replaces pre-T-529 BLOCKER #3 warn-only marker. Live = Bybit
        # GET /v5/market/instruments-info; paper = hardcoded fixture.
        # On QtyValidationError (qty_step / min_order_qty) → log + return
        # early (no Bybit round-trip; no NATS publish; no rate-limit token).
        try:
            instrument_info = await adapter.get_instrument_info(request.symbol)
            quantized_qty = quantize_qty(working_qty, instrument_info)
        except QtyValidationError as exc:
            logger.error(
                "execution.qty_validation_failed",
                bot_id=bot_id,
                symbol=request.symbol,
                constraint=exc.constraint,
                actual_qty=str(exc.actual_qty),
                qty_step=str(exc.info.qty_step),
                min_order_qty=str(exc.info.min_order_qty),
            )
            return
        except (AuthError, NetworkTimeout, RateLimitError) as exc:
            logger.error(
                "execution.get_instrument_info_failed",
                bot_id=bot_id,
                symbol=request.symbol,
                error=str(exc),
            )
            return
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
                quantized_qty,
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
            try:
                fill_price = await adapter.get_fill_price(
                    request.symbol,
                    place_result.exchange_order_id,
                )
            except (AuthError, NetworkTimeout, RateLimitError) as exc:
                # T-216c / H-032: transient adapter error in retry loop must NOT
                # bypass the FillPriceUnresolvedError + DLQ contract. Treat as
                # None: warn-log + retry counter advances + sleep on remaining
                # attempts. After exhaustion, falls through to existing
                # `if fill_price is None:` block (DLQ + raise).
                logger.warning(
                    "execution.get_fill_price_transient_error",
                    bot_id=bot_id,
                    exchange_order_id=place_result.exchange_order_id,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                fill_price = None
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
        tp_size = compute_tp_size(quantized_qty, request.tp_qty_pct)
        notional_usd = compute_notional_usd(quantized_qty, fill_price)

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
            # T-511b2 / ADR-0010: paper-mode shadow.start emit pred early-return.
            # parent_trade_id sourced z OrderPlaceResult.paper_trade_id (PaperExchange
            # populates v _persist_open). Conditional guard: empty variants list OR
            # missing paper_trade_id (defenzívne pre Bybit-cross-contamination ktorá
            # by sa nemala stať lebo Bybit nemal by ísť do paper-fork) skipuje emit.
            if request.shadow_variants and place_result.paper_trade_id is not None:
                await emit_post_commit_shadow_start_event(
                    bus=bus,
                    bot_id=bot_id,
                    correlation_id=envelope.correlation_id,
                    parent_trade_id=place_result.paper_trade_id,
                    parent_kind="paper",
                    symbol=request.symbol,
                    side=request.side,
                    entry_price=fill_price,
                    qty=quantized_qty,
                    shadow_variants=list(request.shadow_variants),
                    bound_logger=logger,
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
                qty=quantized_qty,
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
                order_placed_payload, sl_moved_payload, trade_id = await persist_placement_tx(
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
                    qty=quantized_qty,
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

        # 11.5 T-511b2 / ADR-0010 — live-mode shadow.start emit when bot_config
        # has shadow.enabled (request.shadow_variants populated by strategy-engine
        # consumer.py). parent_kind='live' — parent_trade_id references trades.id.
        if request.shadow_variants:
            await emit_post_commit_shadow_start_event(
                bus=bus,
                bot_id=bot_id,
                correlation_id=envelope.correlation_id,
                parent_trade_id=trade_id,
                parent_kind="live",
                symbol=request.symbol,
                side=request.side,
                entry_price=fill_price,
                qty=quantized_qty,
                shadow_variants=list(request.shadow_variants),
                bound_logger=logger,
            )

        # 12. T-217a — spawn PositionLifecycle monitor task post-emit (§9.5:1585-1592).
        # T-217b — adapter threaded for BE/trail set_trading_stop calls.
        lifecycle_task = asyncio.create_task(
            run_position_monitor_for_trade(
                bot_id=bot_id,
                symbol=request.symbol,
                trade_id=trade_id,
                side=request.side,
                entry_price=fill_price,
                qty=quantized_qty,
                pool=pool,
                bus=bus,
                adapter=adapter,
                bound_logger=logger,
                poll_interval_s=position_poll_interval_s,
                stale_ticks_threshold=position_poll_stale_ticks,
                now_fn=now_fn,
            ),
            name=f"lifecycle_{bot_id}_{trade_id}",
        )
        position_lifecycle_tasks[trade_id] = lifecycle_task

    consumer = OrderRequestDedupConsumer(
        handler=_handle,
        capacity=dedup_capacity,
        bound_logger=logger,
    )
    return consumer.consume
