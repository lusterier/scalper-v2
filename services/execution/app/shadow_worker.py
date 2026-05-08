"""T-511b1: Shadow-worker FSM core (consumer half).

Per-variant simulation against PaperExchange (T-511a) + ADR-0005 v2 H-024
terminal classification + reuse of T-510b shadow persistence. ADR-0009
locks shadow data-stream to ``market.ohlc.1m.>`` (BRIEF §13.3 deviation).
T-511b2 ships producer half + parent-close H-016 hook + parity test.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable  # noqa: TC003 — runtime ctor signature
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from packages.bus import MessageEnvelope  # noqa: TC001 — runtime use in handler dispatch
from packages.bus.payloads import ShadowStartPayload, VariantSpec
from packages.bus.schemas import OhlcCandlePayload
from packages.core import BotId
from packages.core.types import ShadowVariantTerminal
from packages.db.queries.shadow import (
    insert_shadow_variant,
    update_shadow_variant_terminal,
)
from packages.exchange.paper import PaperExchange, SlippageModel, TerminalEvent

if TYPE_CHECKING:
    from packages.bus import BusProtocol

logger = logging.getLogger(__name__)


# Verbatim copy of lifecycle.py:233 / :249 / :260 (3 helpers); T-511b2
# parity test enforces no drift.


def _check_be_trigger(
    side: str,
    current_price: Decimal,
    entry_price: Decimal,
    be_trigger: Decimal,
) -> bool:
    """Long: ``(current - entry) / entry >= be_trigger``; short: mirror."""
    if side == "buy":
        return (current_price - entry_price) / entry_price >= be_trigger
    return (entry_price - current_price) / entry_price >= be_trigger


def _compute_be_sl_price(
    side: str,
    entry_price: Decimal,
    be_sl_level: Decimal,
) -> Decimal:
    """Long: ``entry * (1 + be_sl_level)``; short: ``entry * (1 - be_sl_level)``."""
    if side == "buy":
        return entry_price * (Decimal("1") + be_sl_level)
    return entry_price * (Decimal("1") - be_sl_level)


def _compute_trail_sl_price(
    side: str,
    best_price: Decimal,
    trail_pct: Decimal,
) -> Decimal:
    """Long: ``best * (1 - trail_pct)``; short: ``best * (1 + trail_pct)``."""
    if side == "buy":
        return best_price * (Decimal("1") - trail_pct)
    return best_price * (Decimal("1") + trail_pct)


def _terminal_from_pe_state(
    *,
    exec_type: Literal["sl", "tp"],
    sl_type_at_close: Literal["protective", "be", "trail"],
    tpsl_mode_at_close: Literal["Full", "Partial"],
) -> ShadowVariantTerminal:
    """ADR-0005 v2 H-024 derivation: terminal outcome from PE state snapshot.

    Mirror dispatcher.py:316-380 truth table. Partial-mode w/ residual_qty>0
    leaves position open and never reaches this function.
    """
    if exec_type == "sl":
        if sl_type_at_close == "protective":
            return ShadowVariantTerminal.SL_HIT
        if sl_type_at_close == "be":
            return ShadowVariantTerminal.BE_HIT
        return ShadowVariantTerminal.TP_TRAIL
    return ShadowVariantTerminal.TP_FULL


class ShadowWorker:
    """Subscribes ``shadow.start.>``; spawns N tasks per ShadowStartPayload.

    Each task runs an isolated PaperExchange (seed_open_state per T-511a).
    T-511b2 adds ``_on_parent_close`` H-016 hook on ``trade.closed.>``;
    T-511b1 ships only the ``_active_tasks`` write-side registry.
    """

    def __init__(
        self,
        *,
        bus: BusProtocol,
        pool: Any,
        seed_balance: Decimal,
        slippage_model: SlippageModel,
        slippage_params: dict[str, Decimal],
        fee_rate: Decimal,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._bus = bus
        self._pool = pool
        self._seed_balance = seed_balance
        self._slippage_model = slippage_model
        self._slippage_params = slippage_params
        self._fee_rate = fee_rate
        self._clock = clock
        self._active_tasks: dict[int, list[asyncio.Task[None]]] = {}
        self._shadow_start_sub: object | None = None

    async def start(self) -> None:
        """Subscribe to ``shadow.start.>`` wildcard. Idempotent."""
        if self._shadow_start_sub is not None:
            return
        self._shadow_start_sub = await self._bus.subscribe(
            "shadow.start.>", self._on_shadow_start_envelope
        )

    async def stop(self) -> None:
        """Bus-unsubscribe + cancel all in-flight variant tasks. Idempotent."""
        if self._shadow_start_sub is not None:
            await _unsubscribe(self._shadow_start_sub)
            self._shadow_start_sub = None
        for tasks in list(self._active_tasks.values()):
            for task in tasks:
                if not task.done():
                    task.cancel()
        self._active_tasks.clear()

    async def _on_shadow_start_envelope(self, envelope: MessageEnvelope) -> None:
        """NATS handler: parse envelope → dispatch to typed handler."""
        payload = ShadowStartPayload.model_validate(envelope.payload)
        await self._on_shadow_start(payload)

    async def _on_shadow_start(self, payload: ShadowStartPayload) -> None:
        """Spawn N asyncio tasks per :class:`ShadowStartPayload` variants."""
        tasks: list[asyncio.Task[None]] = []
        for variant in payload.variants:
            task = asyncio.create_task(self._run_shadow_variant(payload=payload, variant=variant))
            tasks.append(task)
        self._active_tasks.setdefault(payload.parent_trade_id, []).extend(tasks)

    async def _run_shadow_variant(
        self,
        *,
        payload: ShadowStartPayload,
        variant: VariantSpec,
    ) -> None:
        """Per-variant async task body. H-016 finalizer in finally block."""
        pe: PaperExchange | None = None
        own_sub: object | None = None
        try:
            ovr = variant.overrides
            be_trigger = Decimal(str(ovr.get("be_trigger", "0")))
            be_sl_level = Decimal(str(ovr.get("be_sl_level", "0")))
            trail_pct = Decimal(str(ovr.get("trail_pct", "0")))
            sl_pct = Decimal(str(ovr.get("sl_pct", "0.005")))
            tp_pct = Decimal(str(ovr.get("tp_pct", "0.01")))
            tp_qty_pct = Decimal(str(ovr.get("tp_qty_pct", "1")))
            max_duration_hours = Decimal(str(ovr.get("max_duration_hours", "4")))

            async with self._pool.acquire() as conn:
                row = await insert_shadow_variant(
                    conn,
                    parent_trade_id=payload.parent_trade_id,
                    bot_id=payload.bot_id,
                    variant_name=variant.name,
                    side=payload.side,
                    entry_price=payload.entry_price,
                    qty=payload.qty,
                    created_at=self._clock(),
                    parent_kind=payload.parent_kind,
                )
            variant_id = row.id

            # T-511a seed_open_state carries initial SL/TP/tp_size/tpsl_mode
            # so PE pre-populates _active_positions BEFORE start_consuming
            # subscribe (eliminates set_trading_stop race + saves a step).
            entry = payload.entry_price
            seed_open_state: dict[str, Any] = {
                "symbol": payload.symbol,
                "side": payload.side,
                "qty": payload.qty,
                "entry_price": entry,
                "trade_id": payload.parent_trade_id,
                "sl_price": entry * (Decimal("1") - sl_pct)
                if payload.side == "buy"
                else entry * (Decimal("1") + sl_pct),
                "tp_price": entry * (Decimal("1") + tp_pct)
                if payload.side == "buy"
                else entry * (Decimal("1") - tp_pct),
                "tp_size": payload.qty * tp_qty_pct,
                "tpsl_mode": "Partial" if tp_qty_pct < Decimal("1") else "Full",
            }

            terminal_future: asyncio.Future[TerminalEvent] = asyncio.Future()

            async def on_terminal(evt: TerminalEvent) -> None:
                if not terminal_future.done():
                    terminal_future.set_result(evt)

            pe = PaperExchange(
                seed_balance=self._seed_balance,
                slippage_model=self._slippage_model,
                fee_rate=self._fee_rate,
                bot_id=BotId(payload.bot_id),
                bus=self._bus,
                slippage_params=self._slippage_params,
                pool=self._pool,
                seed_open_state=seed_open_state,
                terminal_callback=on_terminal,
            )
            await pe.start_consuming()

            variant_state: dict[str, Decimal] = {
                "best_price": entry,
                "worst_price": entry,
            }
            own_sub = await self._bus.subscribe(
                f"market.ohlc.1m.{payload.symbol}",
                _make_candle_handler(
                    pe=pe,
                    side=payload.side,
                    entry_price=entry,
                    be_trigger=be_trigger,
                    be_sl_level=be_sl_level,
                    trail_pct=trail_pct,
                    state=variant_state,
                ),
            )

            timeout_seconds = float(max_duration_hours * Decimal(3600))
            outcome: ShadowVariantTerminal
            realized_pnl: Decimal | None
            try:
                terminal_evt = await asyncio.wait_for(terminal_future, timeout=timeout_seconds)
                outcome = _terminal_from_pe_state(
                    exec_type=terminal_evt.exec_type,
                    sl_type_at_close=terminal_evt.sl_type_at_close,
                    tpsl_mode_at_close=terminal_evt.tpsl_mode_at_close,
                )
                realized_pnl = terminal_evt.realized_pnl
            except TimeoutError:
                outcome = ShadowVariantTerminal.TIMEOUT
                realized_pnl = None

            if payload.side == "buy":
                mfe_pct = float((variant_state["best_price"] - entry) / entry)
                mae_pct = float((entry - variant_state["worst_price"]) / entry)
            else:
                mfe_pct = float((entry - variant_state["best_price"]) / entry)
                mae_pct = float((variant_state["worst_price"] - entry) / entry)

            async with self._pool.acquire() as conn:
                result = await update_shadow_variant_terminal(
                    conn,
                    variant_id=variant_id,
                    terminated_at=self._clock(),
                    terminal_outcome=outcome,
                    realized_pnl=realized_pnl,
                    mfe_pct=mfe_pct,
                    mae_pct=mae_pct,
                )
            if result is None:
                # T-510b cascade-delete race: parent_trade row deleted mid-variant
                # (FK ON DELETE CASCADE → shadow_variants gone). Continue without
                # retry per @non_idempotent shipped convention.
                logger.info(
                    "shadow_variant_update_terminal_cascade_delete_race",
                    extra={
                        "event": "shadow_variant_update_terminal_cascade_delete_race",
                        "variant_id": variant_id,
                        "parent_trade_id": payload.parent_trade_id,
                        "terminal_outcome": outcome.value,
                    },
                )
        finally:
            # H-016 unconditional finalizer per BRIEF §20 policy. try/finally
            # is semantically equivalent to async with for non-context-manager
            # subscriptions — both guarantee finalizer execution on exception.
            if pe is not None:
                await pe.bus_unsubscribe_market_ohlc()
            if own_sub is not None:
                await _unsubscribe(own_sub)
            tasks = self._active_tasks.get(payload.parent_trade_id)
            if tasks is not None:
                current = asyncio.current_task()
                self._active_tasks[payload.parent_trade_id] = [t for t in tasks if t is not current]
                if not self._active_tasks[payload.parent_trade_id]:
                    del self._active_tasks[payload.parent_trade_id]


def _make_candle_handler(
    *,
    pe: PaperExchange,
    side: str,
    entry_price: Decimal,
    be_trigger: Decimal,
    be_sl_level: Decimal,
    trail_pct: Decimal,
    state: dict[str, Decimal],
) -> Callable[[MessageEnvelope], Any]:
    """Closure: per-variant candle handler driving BE-trigger + trail FSM.

    Mirrors lifecycle._step body pattern; pure forwarding to PE primitives.
    """

    async def on_candle(envelope: MessageEnvelope) -> None:
        candle = OhlcCandlePayload.model_validate(envelope.payload)
        if not candle.is_closed:
            return

        if side == "buy":
            if candle.high > state["best_price"]:
                state["best_price"] = candle.high
            if candle.low < state["worst_price"]:
                state["worst_price"] = candle.low
        else:
            if candle.low < state["best_price"]:
                state["best_price"] = candle.low
            if candle.high > state["worst_price"]:
                state["worst_price"] = candle.high

        position = pe._active_positions.get(candle.symbol)
        if position is None:
            return
        current_sl_type = position.get("sl_type", "protective")

        if current_sl_type == "protective" and be_trigger > 0:
            favorable = candle.high if side == "buy" else candle.low
            if _check_be_trigger(side, favorable, entry_price, be_trigger):
                new_be_sl = _compute_be_sl_price(side, entry_price, be_sl_level)
                await pe.set_trading_stop(
                    symbol=candle.symbol,
                    tpsl_mode=position["tpsl_mode"],
                    sl_price=new_be_sl,
                    tp_price=position.get("tp_price"),
                    tp_size=position.get("tp_size"),
                    sl_type="be",
                )
                return

        # Trail-recompute branch — mirror lifecycle.py:200-201 inline trigger.
        if current_sl_type == "trail" and trail_pct > 0:
            new_trail_sl = _compute_trail_sl_price(side, state["best_price"], trail_pct)
            current_sl = position.get("sl_price")
            if current_sl is None:
                return
            tightened = (side == "buy" and new_trail_sl > current_sl) or (
                side == "sell" and new_trail_sl < current_sl
            )
            if tightened:
                await pe.set_trading_stop(
                    symbol=candle.symbol,
                    tpsl_mode=position["tpsl_mode"],
                    sl_price=new_trail_sl,
                    tp_price=position.get("tp_price"),
                    tp_size=position.get("tp_size"),
                )

    return on_candle


async def _unsubscribe(sub: object) -> None:
    """NATS Subscription OR ReplayBus.active=False; mirrors T-511a PE pattern."""
    if hasattr(sub, "unsubscribe") and callable(sub.unsubscribe):
        await sub.unsubscribe()
    elif hasattr(sub, "active"):
        sub.active = False
