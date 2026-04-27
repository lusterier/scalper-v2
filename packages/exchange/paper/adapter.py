"""§12.1 PaperExchange — implements ExchangeClient Protocol (T-201).

T-211 shipped class scaffolding (10 ExchangeClient methods raising
NotImplementedError). T-213a extends with fill semantics + slippage +
fee + market-order compute + SL/TP cross detection — pure computation;
NO DB writes, NO event emission. T-213b will land paper_* persistence
+ execution emission body on top.

T-213a partial-body methods (compute logic; raise NotImplementedError
forward-pointer to T-213b for persistence):

* :meth:`place_market_order` — compute fill_price + slippage + fee.
* :meth:`set_trading_stop` — store sl_price/tp_price/tpsl_mode in
  per-instance active-positions in-memory dict (H-013 propagation).

T-213a internal methods (full body):

* :meth:`start_consuming` — NATS subscribe to ``market.ohlc.1m.>``.
* :meth:`_on_candle` — last-price cache update + SL/TP cross detection.
* :meth:`_check_sl_tp_crosses` — pessimistic SL-first per Q4-A.
* :meth:`_compute_slippage` — dispatch to slippage module per model.

T-213a full-stub methods (T-211 unchanged):

* ``set_leverage``, ``cancel_order``, ``get_positions``,
  ``get_fill_price``, ``get_closed_pnl_cumulative`` — DB reads/writes
  owned by T-213b.
* ``stream_executions``, ``stream_positions`` — async iterator owned
  by T-213b.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — runtime annotation on frozen dataclass
from decimal import Decimal  # noqa: TC003 — runtime annotation on frozen dataclass
from typing import TYPE_CHECKING, Any, Literal

from packages.bus.schemas import OhlcCandlePayload
from packages.core import idempotent, non_idempotent, now_utc

from . import fees, slippage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from packages.bus import MessageEnvelope, NatsClient
    from packages.core import BotId
    from packages.exchange import (
        ExecutionEvent,
        OrderPlaceResult,
        Position,
        PositionEvent,
    )

__all__ = ["PaperExchange", "PendingSLTPFill", "SlippageModel"]


SlippageModel = Literal["fixed_pct", "proportional_to_qty", "half_spread"]

# Allow-list defends against typos in bots.config (T-215 reads slippage_model
# from YAML; pydantic Literal validation catches at config-load time, but the
# constructor allow-list is the second-line guard at adapter-pool composition
# per L-002 / T-210 W#2 precedent).
_SLIPPAGE_MODELS: frozenset[str] = frozenset({"fixed_pct", "proportional_to_qty", "half_spread"})

# Decision #11 (BLOCKER 2): per-model required-keys allow-list for
# slippage_params validation. Defends against pydantic-bypass at T-215
# surfacing as opaque KeyError at first market-order placement.
_REQUIRED_KEYS_PER_MODEL: dict[str, frozenset[str]] = {
    "fixed_pct": frozenset({"fixed_slippage_pct"}),
    "proportional_to_qty": frozenset({"qty_slippage_coeff"}),
    "half_spread": frozenset({"half_spread_factor"}),
}


def _stub_message(method: str) -> str:
    """Forward-pointer message for the T-213b owner task (T-213a partial-body).

    Used only by T-211 full-stub methods (set_leverage, cancel_order,
    get_positions, get_fill_price, get_closed_pnl_cumulative,
    stream_executions, stream_positions) which T-213a leaves untouched.
    """
    return (
        f"PaperExchange.{method} body lands at T-213 "
        f"(fill semantics + paper_* persistence + execution emission)"
    )


@dataclass(frozen=True, slots=True)
class PendingSLTPFill:
    """Decision #12: SL/TP cross-detection event enqueued by T-213a.

    T-213a populates this dataclass on every SL/TP cross detected via
    ``_check_sl_tp_crosses``. T-213b drains the queue → writes
    ``paper_executions`` + emits :class:`packages.exchange.ExecutionEvent`.

    H-013 invariant (Decision #14): ``tpsl_mode`` is propagated from
    ``set_trading_stop`` registration through to this event without
    any ``"Full"`` default baking.
    """

    symbol: str
    side: Literal["buy", "sell"]
    qty: Decimal
    trigger_price: Decimal
    triggered_at: datetime
    kind: Literal["sl", "tp"]
    tpsl_mode: Literal["Full", "Partial"]


class PaperExchange:
    """§12.1 paper-trading exchange simulator with T-213a fill semantics.

    Construction parameters:

    * ``seed_balance: Decimal`` — initial paper-account balance.
    * ``slippage_model: SlippageModel`` — one of ``"fixed_pct"``,
      ``"proportional_to_qty"``, ``"half_spread"`` per §12.1.
    * ``fee_rate: Decimal`` — per-trade fee rate.
    * ``bot_id: BotId`` — identity for paper_* writes (T-213b).
    * ``bus: NatsClient`` — NATS subscriber for ``market.ohlc.1m.>``.
    * ``slippage_params: dict[str, Decimal]`` — per-model coefficient
      keyed by parameter name (validated via
      :data:`_REQUIRED_KEYS_PER_MODEL` at construction).
    * ``now_fn: Callable[[], datetime] = now_utc`` — DI'd clock for
      test-deterministic ``triggered_at`` capture.
    """

    def __init__(
        self,
        *,
        seed_balance: Decimal,
        slippage_model: SlippageModel,
        fee_rate: Decimal,
        bot_id: BotId,
        bus: NatsClient,
        slippage_params: dict[str, Decimal],
        now_fn: Callable[[], datetime] = now_utc,
    ) -> None:
        if slippage_model not in _SLIPPAGE_MODELS:
            raise ValueError(
                f"slippage_model must be one of {sorted(_SLIPPAGE_MODELS)}, got {slippage_model!r}"
            )
        required = _REQUIRED_KEYS_PER_MODEL[slippage_model]
        if set(slippage_params) != required:
            raise ValueError(
                f"slippage_params for {slippage_model!r} must have keys "
                f"{sorted(required)}, got {sorted(slippage_params)}"
            )
        self._seed_balance = seed_balance
        self._slippage_model: SlippageModel = slippage_model
        self._fee_rate = fee_rate
        self._bot_id = bot_id
        self._bus = bus
        self._slippage_params = slippage_params
        self._now_fn = now_fn
        # Per-instance state (§N6: not module-level globals).
        self._last_price: dict[str, Decimal] = {}
        self._last_candle: dict[str, OhlcCandlePayload] = {}
        # Active SL/TP registrations from set_trading_stop (T-213a partial body);
        # T-213b will hydrate from paper_positions table on restart.
        self._active_positions: dict[str, dict[str, Any]] = {}
        # SL/TP cross detection queue; T-213b drains.
        self._pending_sl_tp_fills: list[PendingSLTPFill] = []

    async def start_consuming(self) -> None:
        """Decision #16: subscribe to ``market.ohlc.1m.>`` for SL/TP monitor."""
        await self._bus.subscribe("market.ohlc.1m.>", self._on_candle)

    async def _on_candle(self, envelope: MessageEnvelope) -> None:
        """Update last-price cache + check SL/TP crosses for this symbol."""
        candle = OhlcCandlePayload.model_validate(envelope.payload)
        if not candle.is_closed:
            return
        self._last_candle[candle.symbol] = candle
        self._last_price[candle.symbol] = candle.close
        await self._check_sl_tp_crosses(candle)

    async def _check_sl_tp_crosses(self, candle: OhlcCandlePayload) -> None:
        """Pessimistic SL-first per Q4-A F2 simplification.

        For each active position on this symbol, check if SL or TP price
        falls within candle's [low, high] range. If both cross in the
        same candle, SL fills first (worst-case for paper P&L; F5
        backtest harness §12.2 will replay intra-candle path).
        """
        position = self._active_positions.get(candle.symbol)
        if position is None:
            return
        side = position.get("side")
        qty = position.get("qty")
        sl_price: Decimal | None = position.get("sl_price")
        tp_price: Decimal | None = position.get("tp_price")
        tpsl_mode: Literal["Full", "Partial"] = position["tpsl_mode"]
        # Position not fully initialised (set_trading_stop ran but
        # place_market_order side+qty not yet set at T-213a state).
        if side is None or qty is None:
            return
        if sl_price is not None and candle.low <= sl_price <= candle.high:
            self._pending_sl_tp_fills.append(
                PendingSLTPFill(
                    symbol=candle.symbol,
                    side=side,
                    qty=qty,
                    trigger_price=sl_price,
                    triggered_at=self._now_fn(),
                    kind="sl",
                    tpsl_mode=tpsl_mode,
                )
            )
            return  # SL-first; TP discarded for this candle.
        if tp_price is not None and candle.low <= tp_price <= candle.high:
            tp_qty = position.get("tp_size") if tpsl_mode == "Partial" else qty
            if tp_qty is None:
                raise ValueError(
                    f"Partial TP without tp_size for {candle.symbol}; "
                    f"set_trading_stop must supply tp_size when tpsl_mode='Partial'"
                )
            self._pending_sl_tp_fills.append(
                PendingSLTPFill(
                    symbol=candle.symbol,
                    side=side,
                    qty=tp_qty,
                    trigger_price=tp_price,
                    triggered_at=self._now_fn(),
                    kind="tp",
                    tpsl_mode=tpsl_mode,
                )
            )

    def _compute_slippage(
        self, *, price: Decimal, qty: Decimal, candle: OhlcCandlePayload
    ) -> Decimal:
        """Dispatch to per-model slippage function with bot-config params."""
        if self._slippage_model == "fixed_pct":
            return slippage.fixed_pct(
                price=price,
                fixed_slippage_pct=self._slippage_params["fixed_slippage_pct"],
            )
        if self._slippage_model == "proportional_to_qty":
            return slippage.proportional_to_qty(
                price=price,
                qty=qty,
                qty_slippage_coeff=self._slippage_params["qty_slippage_coeff"],
            )
        return slippage.half_spread(
            high=candle.high,
            low=candle.low,
            half_spread_factor=self._slippage_params["half_spread_factor"],
        )

    @idempotent
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        raise NotImplementedError(_stub_message("set_leverage"))

    @non_idempotent
    async def place_market_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: Decimal,
        reduce_only: bool = False,
    ) -> OrderPlaceResult:
        """T-213a: compute fill_price + slippage + fee. T-213b: persist + emit.

        Hand verification §C in docs/plans/T-213.md.
        """
        last_price = self._last_price.get(symbol)
        if last_price is None:
            raise ValueError(
                f"No last-observed price for {symbol}; "
                f"subscribe to market.ohlc.1m.{symbol} via start_consuming() first"
            )
        last_candle = self._last_candle[symbol]
        slippage_amount = self._compute_slippage(price=last_price, qty=qty, candle=last_candle)
        fill_price = last_price + slippage_amount if side == "buy" else last_price - slippage_amount
        fee = fees.compute_fee(qty=qty, fill_price=fill_price, fee_rate=self._fee_rate)
        raise NotImplementedError(
            f"T-213a computed fill_price={fill_price} fee={fee}; "
            f"T-213b owns paper_orders/paper_executions persistence + execution emission"
        )

    @idempotent
    async def set_trading_stop(
        self,
        symbol: str,
        tpsl_mode: Literal["Full", "Partial"],
        sl_price: Decimal | None = None,
        tp_price: Decimal | None = None,
        tp_size: Decimal | None = None,
    ) -> None:
        """T-213a: store SL/TP/tpsl_mode in active-positions dict. T-213b: persist.

        H-013 invariant (Decision #14): ``tpsl_mode`` propagated to
        active-positions state without ``'Full'`` default baking;
        ``_check_sl_tp_crosses`` reads from this state when constructing
        :class:`PendingSLTPFill`.
        """
        existing = self._active_positions.get(symbol, {})
        existing["sl_price"] = sl_price
        existing["tp_price"] = tp_price
        existing["tp_size"] = tp_size
        existing["tpsl_mode"] = tpsl_mode
        self._active_positions[symbol] = existing
        raise NotImplementedError(
            "T-213a registered SL/TP in memory; "
            "T-213b owns paper_positions persistence + execution emission"
        )

    @idempotent
    async def cancel_order(self, symbol: str, order_id: str) -> None:
        raise NotImplementedError(_stub_message("cancel_order"))

    @idempotent
    async def get_positions(
        self,
        symbol: str | None = None,
    ) -> list[Position]:
        raise NotImplementedError(_stub_message("get_positions"))

    @idempotent
    async def get_fill_price(
        self,
        symbol: str,
        order_id: str,
    ) -> Decimal | None:
        raise NotImplementedError(_stub_message("get_fill_price"))

    @idempotent
    async def get_closed_pnl_cumulative(self, sub_account: str) -> Decimal:
        raise NotImplementedError(_stub_message("get_closed_pnl_cumulative"))

    def stream_executions(self) -> AsyncIterator[ExecutionEvent]:
        raise NotImplementedError(_stub_message("stream_executions"))

    def stream_positions(self) -> AsyncIterator[PositionEvent]:
        raise NotImplementedError(_stub_message("stream_positions"))

    async def close(self) -> None:
        """No-op at skeleton — lifecycle method must work without business logic.

        T-213 may extend with paper-state flush; the skeleton no-op is
        forward-compatible.
        """
