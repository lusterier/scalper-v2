"""§12.1 PaperExchange skeleton — implements ExchangeClient Protocol (T-201).

T-211 ships the class scaffolding: constructor accepts ``seed_balance``,
``slippage_model``, ``fee_rate`` per §9.5 line 1566 + §12.1; every
ExchangeClient method is a stub that raises ``NotImplementedError``
pointing at T-213 (fill semantics + paper_* persistence + execution
emission).

T-213 will wire (per TASKS.md line 82 verbatim):

* market-fill at last observed tick price + slippage per ``slippage_model``.
* SL/TP per-tick monitor (T-200 Q4-A: F2 uses OHLC-1m; intra-candle
  path generation deferred to F5 backtest harness §12.2).
* Fee deduction at fill time per ``fee_rate``.
* paper_orders / paper_trades / paper_executions / paper_positions
  DB writes (T-212 migration 0008).
* Execution emission via a WS-like async iterator that is shape-
  identical to the live BybitV5Adapter stream (T-209) so the T-218
  dispatcher cannot distinguish paper from live.

T-215 (adapter pool composition root) instantiates ``PaperExchange``
for any bot whose ``bots.exchange_mode == 'paper'``. T-218 / T-219 /
T-221 consume the ExchangeClient interface uniformly via the adapter
pool — they do NOT touch PaperExchange internals; T-213 is the sole
owner of paper-side fill logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from packages.core import idempotent, non_idempotent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from decimal import Decimal

    from packages.exchange import (
        ExecutionEvent,
        OrderPlaceResult,
        Position,
        PositionEvent,
    )

__all__ = ["PaperExchange", "SlippageModel"]


SlippageModel = Literal["fixed_pct", "proportional_to_qty", "half_spread"]

# Allow-list defends against typos in bots.config (T-215 reads slippage_model
# from YAML; pydantic Literal validation catches at config-load time, but the
# constructor allow-list is the second-line guard at adapter-pool composition
# per L-002 / T-210 W#2 precedent).
_SLIPPAGE_MODELS: frozenset[str] = frozenset({"fixed_pct", "proportional_to_qty", "half_spread"})


def _stub_message(method: str) -> str:
    """Forward-pointer message for the T-213 owner task.

    Every method stub raises ``NotImplementedError(_stub_message(name))``;
    if T-213 plan-doc author replaces a stub without removing the
    accompanying test, the message-contains-"T-213" assertion in
    ``test_*_stub_raises*`` breaks loudly. That is the intended
    fail-loud forward-pointer contract.
    """
    return (
        f"PaperExchange.{method} body lands at T-213 "
        f"(fill semantics + paper_* persistence + execution emission)"
    )


class PaperExchange:
    """§12.1 paper-trading exchange simulator. Skeleton only at T-211.

    Implements the :class:`packages.exchange.ExchangeClient` Protocol
    (T-201) — every method present with matching idempotency markers
    via :data:`packages.exchange.protocols._UNLABELED_METHODS` exemption
    set. Method bodies raise :class:`NotImplementedError` until T-213
    wires the real fill logic.

    Construction parameters per §9.5 line 1566 + §12.1:

    * ``seed_balance: Decimal`` — initial paper-account balance.
    * ``slippage_model: SlippageModel`` — one of ``"fixed_pct"``,
      ``"proportional_to_qty"``, ``"half_spread"`` per §12.1 line 1937.
    * ``fee_rate: Decimal`` — per-trade fee rate; fees deducted at fill
      time same as live (§12.1).

    Future T-213 constructor extensions (DB pool, NATS bus, KV reader,
    bot_id) are NOT pre-shipped at T-211 per §0.8 anti-hypothetical;
    T-213 plan-doc adds them when their first import lands.
    """

    def __init__(
        self,
        *,
        seed_balance: Decimal,
        slippage_model: SlippageModel,
        fee_rate: Decimal,
    ) -> None:
        if slippage_model not in _SLIPPAGE_MODELS:
            raise ValueError(
                f"slippage_model must be one of {sorted(_SLIPPAGE_MODELS)}, got {slippage_model!r}"
            )
        self._seed_balance = seed_balance
        self._slippage_model: SlippageModel = slippage_model
        self._fee_rate = fee_rate

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
        raise NotImplementedError(_stub_message("place_market_order"))

    @idempotent
    async def set_trading_stop(
        self,
        symbol: str,
        tpsl_mode: Literal["Full", "Partial"],
        sl_price: Decimal | None = None,
        tp_price: Decimal | None = None,
        tp_size: Decimal | None = None,
    ) -> None:
        raise NotImplementedError(_stub_message("set_trading_stop"))

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
