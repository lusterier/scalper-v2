"""§11.2 BybitV5Adapter — ExchangeClient Protocol implementation (T-208a).

T-208a ships 4 write methods + ctor:

* :meth:`set_leverage` — LRU-cached per ``leverage_cache_ttl_s`` (default
  3600s per Q9 brief; operator-tunable per L-001 §N9).
* :meth:`place_market_order` — H-003 zero-retry; ``NetworkTimeout`` →
  ``UnknownState`` per §11.3.
* :meth:`set_trading_stop` — H-013 no-default ``tpsl_mode`` (T-201
  Protocol contract).
* :meth:`cancel_order` — 3x retry per §11.2.

Stub forward-pointers per T-211 stub-pin precedent:

* ``stream_executions`` / ``stream_positions`` / ``close``: T-209.
* ``get_positions`` / ``get_fill_price`` /
  ``get_closed_pnl_cumulative``: T-208b.

Composes T-207 :class:`BybitV5Client` (HTTP envelope + signing + retry
matrix) + T-205 :class:`SharedRateLimiter` (cross-bot IP coordination
per H-025) + optional Prometheus :class:`Counter`
(``rate_limit_hits_total`` per §15.3).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from packages.core import idempotent, non_idempotent, now_utc
from packages.exchange.errors import NetworkTimeout, RateLimitError, UnknownState
from packages.exchange.types import (
    ExecutionEvent,
    OrderPlaceResult,
    Position,
    PositionEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from decimal import Decimal

    from prometheus_client import Counter

    from packages.bus import NatsClient
    from packages.exchange.bybit_v5.client import BybitV5Client
    from packages.exchange.rate_limiter import SharedRateLimiter


__all__ = ["BybitV5Adapter"]


_CATEGORY = "linear"
# Q9 brief default — 1h TTL; operator-tunable via ctor kwarg per L-001 §N9.
_DEFAULT_LEVERAGE_CACHE_TTL_S = 3600.0
_BUY: Literal["Buy"] = "Buy"
_SELL: Literal["Sell"] = "Sell"

logger = logging.getLogger(__name__)


def _to_bybit_side(side: Literal["buy", "sell"]) -> Literal["Buy", "Sell"]:
    return _BUY if side == "buy" else _SELL


def _stub_message(method: str, owner: str) -> str:
    return f"BybitV5Adapter.{method} body lands at {owner}"


class BybitV5Adapter:
    """ExchangeClient implementation against Bybit V5 REST + private WS."""

    def __init__(
        self,
        *,
        client: BybitV5Client,
        limiter: SharedRateLimiter,
        bus: NatsClient,
        sub_account: str,
        metrics_counter: Counter | None = None,
        leverage_cache_ttl_s: float = _DEFAULT_LEVERAGE_CACHE_TTL_S,
        now_fn: Callable[[], datetime] = now_utc,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._bus = bus
        self._sub_account = sub_account
        self._metrics_counter = metrics_counter
        self._leverage_cache_ttl_s = leverage_cache_ttl_s
        self._now_fn = now_fn
        # Per-instance leverage cache: key=(symbol, leverage), value=cached_at.
        self._leverage_cache: dict[tuple[str, int], datetime] = {}

    @idempotent
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """LRU-cached per ``leverage_cache_ttl_s``; cache hit → silent return."""
        key = (symbol, leverage)
        cached_at = self._leverage_cache.get(key)
        if cached_at is not None and (self._now_fn() - cached_at) < timedelta(
            seconds=self._leverage_cache_ttl_s,
        ):
            return
        await self._limiter.acquire(self._sub_account, "positions")
        try:
            await self._client.request(
                "POST",
                "/v5/position/set-leverage",
                body={
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "buyLeverage": str(leverage),
                    "sellLeverage": str(leverage),
                },
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("positions")
            raise
        self._leverage_cache[key] = self._now_fn()

    @non_idempotent
    async def place_market_order(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        qty: Decimal,
        reduce_only: bool = False,
    ) -> OrderPlaceResult:
        """H-003: 0x retry; ``NetworkTimeout`` raises ``UnknownState``."""
        await self._limiter.acquire(self._sub_account, "orders")
        try:
            result = await self._client.request(
                "POST",
                "/v5/order/create",
                body={
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "side": _to_bybit_side(side),
                    "orderType": "Market",
                    "qty": str(qty),
                    "reduceOnly": reduce_only,
                },
                retries=0,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("orders")
            raise
        except NetworkTimeout as exc:
            raise UnknownState("place_market_order") from exc
        return OrderPlaceResult(
            exchange_order_id=str(result["orderId"]),
            placed_at=self._now_fn(),
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
        """H-013: ``tpsl_mode`` no-default per T-201 Protocol contract."""
        body: dict[str, Any] = {
            "category": _CATEGORY,
            "symbol": symbol,
            "tpslMode": tpsl_mode,
        }
        if sl_price is not None:
            body["stopLoss"] = str(sl_price)
        if tp_price is not None:
            body["takeProfit"] = str(tp_price)
        if tp_size is not None:
            body["tpSize"] = str(tp_size)
        await self._limiter.acquire(self._sub_account, "positions")
        try:
            await self._client.request(
                "POST",
                "/v5/position/trading-stop",
                body=body,
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("positions")
            raise

    @idempotent
    async def cancel_order(self, symbol: str, order_id: str) -> None:
        await self._limiter.acquire(self._sub_account, "orders")
        try:
            await self._client.request(
                "POST",
                "/v5/order/cancel",
                body={
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "orderId": order_id,
                },
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("orders")
            raise

    # T-208b stubs ----------------------------------------------------------

    @idempotent
    async def get_positions(
        self,
        symbol: str | None = None,
    ) -> list[Position]:
        raise NotImplementedError(_stub_message("get_positions", "T-208b"))

    @idempotent
    async def get_fill_price(
        self,
        symbol: str,
        order_id: str,
    ) -> Decimal | None:
        raise NotImplementedError(_stub_message("get_fill_price", "T-208b"))

    @idempotent
    async def get_closed_pnl_cumulative(self, sub_account: str) -> Decimal:
        raise NotImplementedError(_stub_message("get_closed_pnl_cumulative", "T-208b"))

    # T-209 stubs -----------------------------------------------------------

    def stream_executions(self) -> AsyncIterator[ExecutionEvent]:
        raise NotImplementedError(_stub_message("stream_executions", "T-209"))

    def stream_positions(self) -> AsyncIterator[PositionEvent]:
        raise NotImplementedError(_stub_message("stream_positions", "T-209"))

    async def close(self) -> None:
        raise NotImplementedError(_stub_message("close", "T-209"))

    # Internals -------------------------------------------------------------

    async def _on_rate_limit_hit(self, group: str) -> None:
        """§15.3 metric increment + ADR-0003 shared pause flag broadcast."""
        if self._metrics_counter is not None:
            self._metrics_counter.labels(exchange="bybit", endpoint_group=group).inc()
        await self._limiter.signal_upstream_rate_limit()
