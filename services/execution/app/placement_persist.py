"""§9.5 steps 6-9 placement persistence + emergency-close helpers (T-216b1+T-216b2).

Owns the persistence-tx + dedup ring + emergency-close machinery for
the order placement pipeline. Imported by ``placement.py`` which extends
the per-bot handler post-fill_price using these helpers.

T-216b1 surface (split from monolithic T-216b per `chore(tasks)` commit
splitting WG#3 LOC checkpoint trigger):

* :func:`compute_sl_price` / :func:`compute_tp_price` /
  :func:`compute_tp_size` / :func:`compute_notional_usd` /
  :func:`opposite_side` — pure Decimal compute helpers (§F.1-§F.4).
* :class:`OrderRequestDedupConsumer` — H-009 family per-bot ring keyed on
  ``(bot_id, signal_id)`` from envelope payload; WG#5 malformed payload
  WARN log inside ``_key_fn``.
* :func:`emergency_close` — H-004 verbatim path: reduce_only opposite-side
  market order + tx persist (open ``orders.status='emergency_closed'`` per
  WG#1 / brief §7.2 line 967 + close ``orders.status='filled'`` +
  ``trades.close_reason='emergency'`` + ``realized_pnl=Decimal('0')``
  placeholder per H-012 closed-pnl source-of-truth invariant; T-220
  reconciles) + 2 trading_events + post-commit emit.

T-216b2 surface (this revision):

* :func:`persist_placement_tx` — happy-path single-tx 5-INSERT helper
  (orders open + trades open + position_state initial + 2 trading_events
  ``order_placed`` + ``sl_moved``); returns ``(OrderPlaced, SLMoved)``
  payloads with patched real ``order_id`` for caller to emit post-commit.
* :func:`emit_post_commit_events` — post-commit publisher for
  ``OrderPlaced`` + ``SLMoved`` to ``orders.events.<bot_id>`` per Q2
  publish-after-persist contract; WG#6 per-publish try/except no-short-circuit.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from packages.bus.dedup import DedupingConsumer
from packages.bus.schemas.orders import (
    OrderClosed,
    OrderPlaced,
    SLMoved,
    subject_for_orders_event,
)
from packages.db.queries.execution import (
    insert_order,
    insert_position_state,
    insert_trade,
    insert_trading_event,
    update_trade_close,
)
from packages.exchange.errors import UnknownState

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from typing import Literal

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import MessageEnvelope, NatsClient
    from packages.bus.schemas.orders import OrderRequest
    from packages.core import BotId, CorrelationId
    from packages.exchange.protocols import ExchangeClient
    from packages.exchange.types import OrderPlaceResult


__all__ = [
    "OrderRequestDedupConsumer",
    "compute_notional_usd",
    "compute_sl_price",
    "compute_tp_price",
    "compute_tp_size",
    "emergency_close",
    "emit_post_commit_events",
    "opposite_side",
    "persist_placement_tx",
]


# ---------------------------------------------------------------------------
# Pure compute helpers (§F.1-§F.4 hand-verifiable)
# ---------------------------------------------------------------------------


def compute_sl_price(
    side: Literal["buy", "sell"],
    fill_price: Decimal,
    sl_pct: Decimal,
) -> Decimal:
    """SL price: long subtracts pct (loss below entry); short adds pct."""
    if side == "buy":
        return fill_price * (Decimal("1") - sl_pct)
    return fill_price * (Decimal("1") + sl_pct)


def compute_tp_price(
    side: Literal["buy", "sell"],
    fill_price: Decimal,
    tp_pct: Decimal,
) -> Decimal:
    """TP price: long adds pct (profit above entry); short subtracts pct."""
    if side == "buy":
        return fill_price * (Decimal("1") + tp_pct)
    return fill_price * (Decimal("1") - tp_pct)


def compute_tp_size(fill_qty: Decimal, tp_qty_pct: Decimal) -> Decimal:
    """Partial TP size = qty x tp_qty_pct (H-013 Partial mode)."""
    return fill_qty * tp_qty_pct


def compute_notional_usd(qty: Decimal, price: Decimal) -> Decimal:
    """notional_usd = qty x price quantize NUMERIC(20,4) per §7.2 line 997."""
    return (qty * price).quantize(Decimal("0.0001"))


def opposite_side(side: Literal["buy", "sell"]) -> Literal["buy", "sell"]:
    """Reduce_only flip for emergency-close direction."""
    return "sell" if side == "buy" else "buy"


# ---------------------------------------------------------------------------
# Dedup consumer (H-009 family — per-bot ring)
# ---------------------------------------------------------------------------


class OrderRequestDedupConsumer(DedupingConsumer["MessageEnvelope"]):
    """Per-bot dedup ring keyed on ``(bot_id, signal_id)`` from envelope payload.

    WG#5: malformed payload (missing ``bot_id`` or ``signal_id``) logs WARN
    and falls back to ``"None:None"`` key — dedup-rings malformed payloads
    after the first; OrderRequest.model_validate downstream catches schema
    failures.
    """

    def __init__(
        self,
        *,
        handler: Callable[[MessageEnvelope], Awaitable[None]],
        capacity: int,
        bound_logger: BoundLogger,
    ) -> None:
        self._bound_logger = bound_logger
        super().__init__(
            key_fn=self._extract_key,
            capacity=capacity,
            logger=bound_logger,
        )
        self._handler = handler

    def _extract_key(self, envelope: MessageEnvelope) -> str:
        bot_id = envelope.payload.get("bot_id")
        signal_id = envelope.payload.get("signal_id")
        if bot_id is None or signal_id is None:
            self._bound_logger.warning(
                "execution.dedup_key_extractor_malformed_payload",
                bot_id=str(bot_id),
                signal_id=str(signal_id),
            )
        return f"{bot_id}:{signal_id}"

    async def _process(self, message: MessageEnvelope) -> None:
        await self._handler(message)


# ---------------------------------------------------------------------------
# H-004 emergency-close path
# ---------------------------------------------------------------------------


async def emergency_close(
    *,
    adapter: ExchangeClient,
    bus: NatsClient,
    pool: asyncpg.Pool,
    bound_logger: BoundLogger,
    bot_id: BotId,
    request: OrderRequest,
    envelope: MessageEnvelope,
    place_result: OrderPlaceResult,
    fill_price: Decimal,
    now_fn: Callable[[], datetime],
) -> None:
    """H-004 emergency-close: reduce_only opposite-side market + tx persist closed trade.

    Brief 'estimated fee P&L' deferred to T-220 reconciler per H-012
    closed-pnl source-of-truth invariant; placeholder ``Decimal('0')``
    avoids transient mis-reporting before cumulative-delta from T-219 lands.
    """
    close_at = now_fn()
    close_exchange_order_id: str
    try:
        close_result = await adapter.place_market_order(
            request.symbol,
            opposite_side(request.side),
            request.qty,
            reduce_only=True,
        )
        close_exchange_order_id = close_result.exchange_order_id
    except UnknownState as exc:
        bound_logger.error(
            "execution.emergency_close_place_market_unknown_state",
            bot_id=bot_id,
            symbol=request.symbol,
            error=str(exc),
        )
        return

    notional = compute_notional_usd(request.qty, fill_price)
    placed_at = place_result.placed_at
    order_placed_payload = OrderPlaced(
        bot_id=str(bot_id),
        order_id=0,
        exchange_order_id=place_result.exchange_order_id,
        symbol=request.symbol,
        timestamp=placed_at,
    )
    order_closed_payload = OrderClosed(
        bot_id=str(bot_id),
        order_id=0,
        exchange_order_id=close_exchange_order_id,
        symbol=request.symbol,
        timestamp=close_at,
        realized_pnl=Decimal("0"),
        close_reason="emergency",
    )

    try:
        async with pool.acquire() as conn, conn.transaction():
            open_order_id = await insert_order(
                conn,
                bot_id=str(bot_id),
                signal_id=request.signal_id,
                correlation_id=str(envelope.correlation_id),
                exchange_order_id=place_result.exchange_order_id,
                exchange="bybit",
                symbol=request.symbol,
                side=request.side,
                order_type="market",
                qty=request.qty,
                price=fill_price,
                status="emergency_closed",
                requested_at=envelope.published_at,
                filled_at=placed_at,
                closed_at=close_at,
                idempotent_flag=False,
            )
            close_order_id = await insert_order(
                conn,
                bot_id=str(bot_id),
                signal_id=request.signal_id,
                correlation_id=str(envelope.correlation_id),
                exchange_order_id=close_exchange_order_id,
                exchange="bybit",
                symbol=request.symbol,
                side=opposite_side(request.side),
                order_type="market",
                qty=request.qty,
                price=fill_price,
                status="filled",
                requested_at=close_at,
                filled_at=close_at,
                closed_at=close_at,
                idempotent_flag=False,
            )
            trade_id = await insert_trade(
                conn,
                bot_id=str(bot_id),
                signal_id=request.signal_id,
                open_order_id=open_order_id,
                symbol=request.symbol,
                side=request.side,
                entry_price=fill_price,
                qty=request.qty,
                notional_usd=notional,
                opened_at=placed_at,
            )
            await update_trade_close(
                conn,
                trade_id=trade_id,
                exit_price=fill_price,
                realized_pnl=Decimal("0"),
                fees_paid=Decimal("0"),
                closed_at=close_at,
                close_reason="emergency",
                close_order_id=close_order_id,
            )
            order_placed_payload = order_placed_payload.model_copy(
                update={"order_id": open_order_id}
            )
            order_closed_payload = order_closed_payload.model_copy(
                update={"order_id": close_order_id}
            )
            await insert_trading_event(
                conn,
                occurred_at=placed_at,
                bot_id=str(bot_id),
                correlation_id=str(envelope.correlation_id),
                event_type="order_placed",
                payload=order_placed_payload.model_dump(mode="json"),
            )
            await insert_trading_event(
                conn,
                occurred_at=close_at,
                bot_id=str(bot_id),
                correlation_id=str(envelope.correlation_id),
                event_type="order_closed",
                payload=order_closed_payload.model_dump(mode="json"),
            )
    except Exception as exc:
        bound_logger.error(
            "execution.emergency_close_persist_failed",
            bot_id=bot_id,
            symbol=request.symbol,
            error=str(exc),
        )
        return

    bound_logger.warning(
        "execution.emergency_close_pnl_pending_audit_reconcile",
        bot_id=bot_id,
        symbol=request.symbol,
        close_order_id=close_order_id,
    )
    # WG#6: per-publish try/except so 1st publish failure does NOT short-circuit 2nd.
    from packages.bus import MessageEnvelope as _Env  # local import — avoid cycle

    for event_type, payload in (
        ("order_placed", order_placed_payload),
        ("order_closed", order_closed_payload),
    ):
        try:
            publish_envelope = _Env(
                correlation_id=envelope.correlation_id,
                publisher="execution-service",
                payload=payload.model_dump(mode="json"),
            )
            await bus.publish(subject_for_orders_event(bot_id), publish_envelope)
        except Exception as exc:
            bound_logger.error(
                "execution.event_publish_failed",
                bot_id=bot_id,
                event_type=event_type,
                error=str(exc),
            )


# ---------------------------------------------------------------------------
# Happy-path persistence + post-commit emit (T-216b2; §9.5 steps 8-9)
# ---------------------------------------------------------------------------


async def persist_placement_tx(
    *,
    conn: asyncpg.Connection[asyncpg.Record] | asyncpg.pool.PoolConnectionProxy[asyncpg.Record],
    bot_id: BotId,
    request: OrderRequest,
    envelope: MessageEnvelope,
    place_result: OrderPlaceResult,
    fill_price: Decimal,
    sl_price: Decimal,
    tp_price: Decimal,
    tp_size: Decimal,
    notional_usd: Decimal,
    sl_set_at: datetime,
) -> tuple[OrderPlaced, SLMoved]:
    """Single-tx 5-INSERT for happy-path placement (§9.5 step 8).

    Caller wraps in ``async with pool.acquire() as conn, conn.transaction():``.
    Inserts open ``orders`` row (status='filled'), open ``trades`` row,
    initial ``position_state`` (sl_type='protective'), and 2 ``trading_events``
    rows (``order_placed`` + ``sl_moved``). Returns ``(OrderPlaced, SLMoved)``
    payloads with real ``order_id`` patched post-INSERT — caller emits
    post-commit via :func:`emit_post_commit_events`.
    """
    open_order_id = await insert_order(
        conn,
        bot_id=str(bot_id),
        signal_id=request.signal_id,
        correlation_id=str(envelope.correlation_id),
        exchange_order_id=place_result.exchange_order_id,
        exchange="bybit",
        symbol=request.symbol,
        side=request.side,
        order_type="market",
        qty=request.qty,
        price=fill_price,
        status="filled",
        requested_at=envelope.published_at,
        filled_at=place_result.placed_at,
        closed_at=None,
        idempotent_flag=False,
    )
    trade_id = await insert_trade(
        conn,
        bot_id=str(bot_id),
        signal_id=request.signal_id,
        open_order_id=open_order_id,
        symbol=request.symbol,
        side=request.side,
        entry_price=fill_price,
        qty=request.qty,
        notional_usd=notional_usd,
        opened_at=place_result.placed_at,
    )
    await insert_position_state(
        conn,
        bot_id=str(bot_id),
        symbol=request.symbol,
        trade_id=trade_id,
        side=request.side,
        entry_price=fill_price,
        qty=request.qty,
        remaining_qty=request.qty,
        sl_price=sl_price,
        tp_price=tp_price,
        sl_type="protective",
        updated_at=sl_set_at,
    )
    order_placed_payload = OrderPlaced(
        bot_id=str(bot_id),
        order_id=open_order_id,
        exchange_order_id=place_result.exchange_order_id,
        symbol=request.symbol,
        timestamp=place_result.placed_at,
    )
    sl_moved_payload = SLMoved(
        bot_id=str(bot_id),
        order_id=open_order_id,
        exchange_order_id=place_result.exchange_order_id,
        symbol=request.symbol,
        timestamp=sl_set_at,
        new_sl_price=sl_price,
        sl_type="protective",
    )
    await insert_trading_event(
        conn,
        occurred_at=place_result.placed_at,
        bot_id=str(bot_id),
        correlation_id=str(envelope.correlation_id),
        event_type="order_placed",
        payload=order_placed_payload.model_dump(mode="json"),
    )
    await insert_trading_event(
        conn,
        occurred_at=sl_set_at,
        bot_id=str(bot_id),
        correlation_id=str(envelope.correlation_id),
        event_type="sl_moved",
        payload=sl_moved_payload.model_dump(mode="json"),
    )
    return order_placed_payload, sl_moved_payload


async def emit_post_commit_events(
    *,
    bus: NatsClient,
    bot_id: BotId,
    correlation_id: CorrelationId,
    order_placed_payload: OrderPlaced,
    sl_moved_payload: SLMoved,
    bound_logger: BoundLogger,
) -> None:
    """Post-commit publisher for ``OrderPlaced`` + ``SLMoved`` (§9.5 step 9).

    WG#6: per-publish try/except so a first publish failure does NOT
    short-circuit the second publish. Mirror :func:`emergency_close`
    publish loop. Subject: ``orders.events.<bot_id>``.
    """
    from packages.bus import MessageEnvelope as _Env  # local import — avoid cycle

    for event_type, payload in (
        ("order_placed", order_placed_payload),
        ("sl_moved", sl_moved_payload),
    ):
        try:
            publish_envelope = _Env(
                correlation_id=correlation_id,
                publisher="execution-service",
                payload=payload.model_dump(mode="json"),
            )
            await bus.publish(subject_for_orders_event(bot_id), publish_envelope)
        except Exception as exc:
            bound_logger.error(
                "execution.event_publish_failed",
                bot_id=bot_id,
                event_type=event_type,
                error=str(exc),
            )
