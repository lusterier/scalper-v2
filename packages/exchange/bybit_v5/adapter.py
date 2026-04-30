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
from decimal import Decimal
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

    from prometheus_client import Counter

    from packages.bus import NatsClient
    from packages.exchange.bybit_v5.client import BybitV5Client
    from packages.exchange.bybit_v5.ws import BybitV5PrivateWs
    from packages.exchange.rate_limiter import SharedRateLimiter


__all__ = ["BybitV5Adapter"]


_CATEGORY = "linear"
# Q9 brief default — 1h TTL; operator-tunable via ctor kwarg per L-001 §N9.
_DEFAULT_LEVERAGE_CACHE_TTL_S = 3600.0
_BUY: Literal["Buy"] = "Buy"
_SELL: Literal["Sell"] = "Sell"
# T-208b: F2 single-bot scale ceiling (Bybit limit=200 x 10 = 2000 closed
# trades cumulative). Per L-001 active control: protocol-binding-ish per
# Bybit's own limit cap, NOT operationally tunable (mirror T-205
# _MAX_CAS_RETRIES=3 precedent; F5+ refactors to streaming aggregator).
_MAX_CLOSED_PNL_PAGES = 10

logger = logging.getLogger(__name__)


def _to_bybit_side(side: Literal["buy", "sell"]) -> Literal["Buy", "Sell"]:
    return _BUY if side == "buy" else _SELL


def _map_position_row(item: dict[str, Any]) -> Position:
    """Map Bybit V5 ``/v5/position/list`` ``result.list[]`` row -> Position.

    Per OQ-3 default A: Bybit empty-string convention for flat positions:

    * ``side == ""`` -> ``side=None``.
    * ``avgPrice == ""`` -> ``entry_price=None`` (string ``"0"`` preserved
      as ``Decimal("0")`` per W#3 — recently-closed flat may legitimately
      report avgPrice=``"0"``).
    * ``unrealisedPnl == ""`` -> ``unrealized_pnl=None`` (W#3: ``"0"`` is
      a valid zero-PnL state; preserve as ``Decimal("0")``).

    H-015 round-trip: ``Decimal(item["size"])`` preserves wire string
    exactness (no float coercion).
    """
    side_raw = item["side"]
    if side_raw == _BUY:
        side: Literal["buy", "sell"] | None = "buy"
    elif side_raw == _SELL:
        side = "sell"
    else:
        side = None
    avg_price = item.get("avgPrice")
    entry_price = Decimal(str(avg_price)) if avg_price not in ("", None) else None
    leverage_raw = item.get("leverage")
    leverage = int(str(leverage_raw)) if leverage_raw not in ("", None) else None
    unrealized_raw = item.get("unrealisedPnl")
    unrealized_pnl = Decimal(str(unrealized_raw)) if unrealized_raw not in ("", None) else None
    return Position(
        symbol=item["symbol"],
        side=side,
        size=Decimal(item["size"]),
        entry_price=entry_price,
        leverage=leverage,
        unrealized_pnl=unrealized_pnl,
    )


class BybitV5Adapter:
    """ExchangeClient implementation against Bybit V5 REST + private WS."""

    def __init__(
        self,
        *,
        client: BybitV5Client,
        ws: BybitV5PrivateWs,
        limiter: SharedRateLimiter,
        bus: NatsClient,
        sub_account: str,
        metrics_counter: Counter | None = None,
        leverage_cache_ttl_s: float = _DEFAULT_LEVERAGE_CACHE_TTL_S,
        now_fn: Callable[[], datetime] = now_utc,
    ) -> None:
        self._client = client
        self._ws = ws
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

    # T-208b read methods ---------------------------------------------------

    @idempotent
    async def get_positions(
        self,
        symbol: str | None = None,
    ) -> list[Position]:
        params: dict[str, Any] = {"category": _CATEGORY}
        if symbol is not None:
            params["symbol"] = symbol
        await self._limiter.acquire(self._sub_account, "positions")
        try:
            result = await self._client.request(
                "GET",
                "/v5/position/list",
                params=params,
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("positions")
            raise
        return [_map_position_row(item) for item in result.get("list", [])]

    @idempotent
    async def get_fill_price(
        self,
        symbol: str,
        order_id: str,
    ) -> Decimal | None:
        await self._limiter.acquire(self._sub_account, "orders")
        try:
            result = await self._client.request(
                "GET",
                "/v5/execution/list",
                params={
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "orderId": order_id,
                },
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("orders")
            raise
        items = result.get("list", [])
        if not items:
            return None
        return Decimal(items[0]["execPrice"])

    @idempotent
    async def get_closed_pnl_cumulative(self, sub_account: str) -> Decimal:
        """OQ-5 default A: sum closedPnl over all pages (cap _MAX_CLOSED_PNL_PAGES).

        Sub_account validation BEFORE limiter.acquire per OQ-10 default A —
        ValueError on caller mistake costs no rate-limit token.
        """
        if sub_account != self._sub_account:
            raise ValueError(
                f"sub_account mismatch: got {sub_account!r}, expected {self._sub_account!r}",
            )
        total = Decimal("0")
        cursor: str | None = None
        for _page in range(_MAX_CLOSED_PNL_PAGES):
            params: dict[str, Any] = {"category": _CATEGORY, "limit": 200}
            if cursor is not None:
                params["cursor"] = cursor
            await self._limiter.acquire(self._sub_account, "positions")
            try:
                result = await self._client.request(
                    "GET",
                    "/v5/position/closed-pnl",
                    params=params,
                    retries=3,
                )
            except RateLimitError:
                await self._on_rate_limit_hit("positions")
                raise
            for item in result.get("list", []):
                total += Decimal(item["closedPnl"])
            cursor = result.get("nextPageCursor") or None
            if not cursor:
                return total
        logger.warning(
            "bybit_v5.closed_pnl_pagination_capped_at_max_pages",
            extra={"max_pages": _MAX_CLOSED_PNL_PAGES, "sub_account": sub_account},
        )
        return total

    # T-209 stream + close (delegates to BybitV5PrivateWs) ------------------

    def stream_executions(self) -> AsyncIterator[ExecutionEvent]:
        return self._ws.executions()

    def stream_positions(self) -> AsyncIterator[PositionEvent]:
        return self._ws.positions()

    async def close(self) -> None:
        await self._ws.close()
        await self._client.close()

    # Internals -------------------------------------------------------------

    async def _on_rate_limit_hit(self, group: str) -> None:
        """§15.3 metric increment + ADR-0003 shared pause flag broadcast."""
        if self._metrics_counter is not None:
            self._metrics_counter.labels(exchange="bybit", endpoint_group=group).inc()
        await self._limiter.signal_upstream_rate_limit()
