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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from packages.core import idempotent, non_idempotent, now_utc
from packages.exchange.errors import (
    ExchangeError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
    UnknownState,
)
from packages.exchange.types import (
    AccountBalance,
    ExecutionEvent,
    FundingFee,
    InstrumentInfo,
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
# T-550: Bybit V5 GET /v5/position/list (category=linear) requires symbol OR
# settleCoin. v2 platform scope = USDT-margined linear perps (every configured
# symbol BTCUSDT/ETHUSDT/SOLUSDT is USDT-settled; mirror _CATEGORY="linear").
# Per L-001 active control: market/protocol-binding constant, NOT operationally
# tunable (mirror _MAX_CLOSED_PNL_PAGES precedent). Non-USDT linear perps would
# need symbol-derived/configurable settleCoin — out of scope (future
# market-scope task), not this fix.
_SETTLE_COIN = "USDT"
# Q9 brief default — 1h TTL; operator-tunable via ctor kwarg per L-001 §N9.
_DEFAULT_LEVERAGE_CACHE_TTL_S = 3600.0
_DEFAULT_INSTRUMENTS_INFO_CACHE_TTL_S = 3600.0  # T-529 / H-036; mirror leverage TTL
_BUY: Literal["Buy"] = "Buy"
_SELL: Literal["Sell"] = "Sell"
# T-208b: F2 single-bot scale ceiling (Bybit limit=200 x 10 = 2000 closed
# trades cumulative). Per L-001 active control: protocol-binding-ish per
# Bybit's own limit cap, NOT operationally tunable (mirror T-205
# _MAX_CAS_RETRIES=3 precedent; F5+ refactors to streaming aggregator).
_MAX_CLOSED_PNL_PAGES = 10
_MAX_FUNDING_PAGES = 10  # T-532a — mirror _MAX_CLOSED_PNL_PAGES (Bybit page cap)

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
    * ``stopLoss`` (T-534a) -> ``sl_price``: blank/``""``/``"0"``/
      ``"0.00"``/non-positive -> ``None``. **Deliberate divergence from
      the sibling ``avgPrice "0"``-preserve rule above**: an exchange
      stop-loss at price 0 is semantically impossible — Bybit's no-SL
      sentinel on ``stopLoss`` is exactly a blank or non-positive value,
      so collapsing it to ``None`` is the correct read-semantic for the
      T-534b watchdog (vs ``avgPrice "0"`` which is a legitimate
      recently-closed-flat snapshot value).

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
    # T-534a: no-float decode (mirror entry_price/unrealized_pnl above);
    # non-positive/blank stopLoss is Bybit's no-SL sentinel -> None (see
    # docstring: deliberate divergence from avgPrice "0"-preserve W#3).
    sl_raw = item.get("stopLoss")
    _sl = Decimal(str(sl_raw)) if sl_raw not in ("", None) else None
    sl_price = _sl if (_sl is not None and _sl > 0) else None
    return Position(
        symbol=item["symbol"],
        side=side,
        size=Decimal(item["size"]),
        entry_price=entry_price,
        leverage=leverage,
        unrealized_pnl=unrealized_pnl,
        sl_price=sl_price,
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
        instruments_info_cache_ttl_s: float = _DEFAULT_INSTRUMENTS_INFO_CACHE_TTL_S,
        now_fn: Callable[[], datetime] = now_utc,
    ) -> None:
        self._client = client
        self._ws = ws
        self._limiter = limiter
        self._bus = bus
        self._sub_account = sub_account
        self._metrics_counter = metrics_counter
        self._leverage_cache_ttl_s = leverage_cache_ttl_s
        self._instruments_info_cache_ttl_s = instruments_info_cache_ttl_s
        self._now_fn = now_fn
        # Per-instance leverage cache: key=(symbol, leverage), value=cached_at.
        self._leverage_cache: dict[tuple[str, int], datetime] = {}
        # T-529 / H-036: per-instance instruments-info cache.
        # key=symbol, value=(InstrumentInfo, cached_at).
        self._instruments_info_cache: dict[str, tuple[InstrumentInfo, datetime]] = {}

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
        else:
            params["settleCoin"] = _SETTLE_COIN
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
        """Return VWAP across all execution items for ``order_id`` per H-035.

        T-538 / H-035: VWAP = Σ(price * qty) / Σ(qty) across ALL items in the
        single-page response. Pre-T-538 returned items[0]["execPrice"] only —
        wrong for partial fills that swept multiple price levels. HTTP request
        sets explicit ``limit=100`` (Bybit doc max for ``/v5/execution/list``).
        If response carries ``nextPageCursor`` (truncation indicator for >100
        partial fills, extremely rare), warn-log via
        ``bybit_v5.get_fill_price_paginated_truncation``. Empty list → None
        (caller's T-216c retry path handles). Defensive: zero total qty →
        None + ``bybit_v5.get_fill_price_zero_total_qty`` warn (should never
        fire — every Bybit exec row has qty > 0).
        """
        await self._limiter.acquire(self._sub_account, "orders")
        try:
            result = await self._client.request(
                "GET",
                "/v5/execution/list",
                params={
                    "category": _CATEGORY,
                    "symbol": symbol,
                    "orderId": order_id,
                    "limit": 100,
                },
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("orders")
            raise
        items = result.get("list", [])
        if not items:
            return None
        if result.get("nextPageCursor"):
            logger.warning(
                "bybit_v5.get_fill_price_paginated_truncation",
                extra={
                    "symbol": symbol,
                    "order_id": order_id,
                    "page_size": len(items),
                },
            )
        numerator = Decimal("0")
        denominator = Decimal("0")
        for item in items:
            price = Decimal(item["execPrice"])
            qty = Decimal(item["execQty"])
            numerator += price * qty
            denominator += qty
        if denominator == 0:
            logger.warning(
                "bybit_v5.get_fill_price_zero_total_qty",
                extra={
                    "symbol": symbol,
                    "order_id": order_id,
                    "item_count": len(items),
                },
            )
            return None
        return numerator / denominator

    @idempotent
    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        """Return cached :class:`InstrumentInfo` for ``symbol``.

        T-529 / H-036 — pre-flight qty validation source. Mirrors
        :meth:`set_leverage` cache pattern: dict-keyed on symbol; check
        ``_now_fn() - timestamp <= ttl`` BEFORE upstream call. Cache size
        bounded by symbol diversity (typically <20 in operator's bots.yaml).

        HTTP: GET /v5/market/instruments-info?category=linear&symbol=<symbol>.
        Response shape: ``result.list[0].lotSizeFilter.{qtyStep, minOrderQty,
        minNotionalValue}``. Decimal arithmetic preserved per §5.3.

        Empty list response → :class:`OrderRejected` (instrument not found
        on exchange — delisted or typo'd symbol).
        """
        cached = self._instruments_info_cache.get(symbol)
        if cached is not None and (self._now_fn() - cached[1]) < timedelta(
            seconds=self._instruments_info_cache_ttl_s,
        ):
            return cached[0]
        await self._limiter.acquire(self._sub_account, "market")
        try:
            result = await self._client.request(
                "GET",
                "/v5/market/instruments-info",
                params={"category": _CATEGORY, "symbol": symbol},
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("market")
            raise
        items = result.get("list", [])
        if not items:
            msg = f"instrument not found on exchange: {symbol}"
            raise OrderRejected(msg)
        raw = items[0]
        lot = raw["lotSizeFilter"]
        info = InstrumentInfo(
            symbol=symbol,
            qty_step=Decimal(lot["qtyStep"]),
            min_order_qty=Decimal(lot["minOrderQty"]),
            min_notional_usd=Decimal(lot.get("minNotionalValue", "0")),
        )
        self._instruments_info_cache[symbol] = (info, self._now_fn())
        return info

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

    @idempotent
    async def get_closed_pnl_window(self, sub_account: str, since: datetime) -> Decimal:
        """T-220a — time-windowed sum companion to :meth:`get_closed_pnl_cumulative`.

        Bybit V5 ``/v5/position/closed-pnl`` accepts ``startTime`` (Unix ms);
        sums ``closedPnl`` rows where ``execTime >= since_ms``. Caller (T-220b
        audit) computes ``since = now_utc() - timedelta(seconds=window_s)``.

        Sub_account validation BEFORE limiter.acquire per OQ-10 default A.
        UTC contract: caller MUST pass aware UTC datetime; ``int(since.timestamp() * 1000)``
        converts to Unix ms.
        """
        if sub_account != self._sub_account:
            raise ValueError(
                f"sub_account mismatch: got {sub_account!r}, expected {self._sub_account!r}",
            )
        since_ms = int(since.timestamp() * 1000)
        total = Decimal("0")
        cursor: str | None = None
        for _page in range(_MAX_CLOSED_PNL_PAGES):
            params: dict[str, Any] = {
                "category": _CATEGORY,
                "limit": 200,
                "startTime": since_ms,
            }
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
            "bybit_v5.closed_pnl_window_pagination_capped_at_max_pages",
            extra={
                "max_pages": _MAX_CLOSED_PNL_PAGES,
                "sub_account": sub_account,
                "since": since.isoformat(),
            },
        )
        return total

    @idempotent
    async def get_funding_fees_window(self, sub_account: str, since: datetime) -> list[FundingFee]:
        """T-532a — funding settlements in ``[since, now]`` (T-532b first consumer).

        Bybit V5 ``GET /v5/asset/transaction-log`` ``type=SETTLEMENT``
        accepts ``startTime`` (Unix ms); one :class:`FundingFee` per row.
        Verbatim-mirrors :meth:`get_closed_pnl_window` (the
        windowed-paginated-pull sibling): sub_account validation BEFORE
        ``limiter.acquire`` (OQ-10 default A), ``int(since.timestamp()*1000)``
        UTC→ms, cursor pagination capped at ``_MAX_FUNDING_PAGES``,
        ``RateLimitError → _on_rate_limit_hit → raise``.

        Limiter key ``"positions"`` — ``/v5/asset/*`` shares the ``positions``
        group exactly as ``get_account_balance``'s ``/v5/account/*`` does (no
        new ``EndpointGroup`` — verbatim that documented precedent). ``funding``
        decode via ``Decimal(str(...))`` (§5.13 — no float; money carries
        float-risk, the ``get_account_balance`` discipline, NOT
        ``get_closed_pnl_window``'s bare ``Decimal()``). ``transactionTime``
        Unix-ms → UTC ``datetime`` via the ws.py:123/149 codebase convention
        ``datetime.fromtimestamp(int(...) / 1000, tz=UTC)``. Signed: negative
        = funding paid, positive = received. Returns ``list[FundingFee]``
        (deliberate divergence from :meth:`get_closed_pnl_window`'s
        ``-> Decimal`` aggregate — T-532b stores per-settlement rows +
        feeds the T-220 cumulative-delta a separate funding term, OQ-1/3=A).
        """
        if sub_account != self._sub_account:
            raise ValueError(
                f"sub_account mismatch: got {sub_account!r}, expected {self._sub_account!r}",
            )
        since_ms = int(since.timestamp() * 1000)
        fees: list[FundingFee] = []
        cursor: str | None = None
        for _page in range(_MAX_FUNDING_PAGES):
            params: dict[str, Any] = {
                "category": _CATEGORY,
                "type": "SETTLEMENT",
                "limit": 200,
                "startTime": since_ms,
            }
            if cursor is not None:
                params["cursor"] = cursor
            await self._limiter.acquire(self._sub_account, "positions")
            try:
                result = await self._client.request(
                    "GET",
                    "/v5/asset/transaction-log",
                    params=params,
                    retries=3,
                )
            except RateLimitError:
                await self._on_rate_limit_hit("positions")
                raise
            for item in result.get("list", []):
                fees.append(
                    FundingFee(
                        symbol=item["symbol"],
                        settled_at=datetime.fromtimestamp(
                            int(item["transactionTime"]) / 1000, tz=UTC
                        ),
                        funding=Decimal(str(item["funding"])),
                    )
                )
            cursor = result.get("nextPageCursor") or None
            if not cursor:
                return fees
        logger.warning(
            "bybit_v5.funding_fees_window_pagination_capped_at_max_pages",
            extra={
                "max_pages": _MAX_FUNDING_PAGES,
                "sub_account": sub_account,
                "since": since.isoformat(),
            },
        )
        return fees

    @idempotent
    async def get_account_balance(self, sub_account: str) -> AccountBalance:
        """T-530 — UNIFIED account-level wallet-balance snapshot.

        OQ-1=A: ``GET /v5/account/wallet-balance?accountType=UNIFIED`` →
        ``result.list[0]`` account-level totals mapped 1:1 to the 5
        :class:`AccountBalance` fields. Sub_account validation BEFORE
        limiter.acquire per OQ-10 default A (verbatim mirror
        :meth:`get_closed_pnl_cumulative` — caller mistake costs no token).

        Limiter key ``"positions"`` (same bucket as the account-level
        ``get_closed_pnl_cumulative`` read; ``/v5/account/*`` shares the
        ``positions`` group — no new ``EndpointGroup``). Decimal decode via
        ``Decimal(str(...))`` (§5.13 — no float; ``str()`` is a float-artefact
        guard, account totals carry higher float-risk than lotSize). Empty
        ``result.list`` → :class:`ExchangeError` (a valid authed UNIFIED key
        always returns ``list[0]``; empty = auth/account anomaly — NOT
        :class:`OrderRejected` which is order-semantic).
        """
        if sub_account != self._sub_account:
            raise ValueError(
                f"sub_account mismatch: got {sub_account!r}, expected {self._sub_account!r}",
            )
        await self._limiter.acquire(self._sub_account, "positions")
        try:
            result = await self._client.request(
                "GET",
                "/v5/account/wallet-balance",
                params={"accountType": "UNIFIED"},
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("positions")
            raise
        items = result.get("list", [])
        if not items:
            msg = "wallet-balance: empty account list (auth/account anomaly)"
            raise ExchangeError(msg)
        acct = items[0]
        return AccountBalance(
            wallet_balance=Decimal(str(acct["totalWalletBalance"])),
            available_balance=Decimal(str(acct["totalAvailableBalance"])),
            total_equity=Decimal(str(acct["totalEquity"])),
            margin_balance=Decimal(str(acct["totalMarginBalance"])),
            unrealized_pnl=Decimal(str(acct["totalPerpUPL"])),
        )

    @idempotent
    async def get_mark_price(self, symbol: str) -> Decimal:
        """Return the live Bybit mark price for ``symbol`` (T-527b1).

        T-527b2 §B.1 sizing reference price (notional ÷ mark_price → qty).
        Mirrors :meth:`get_instrument_info`'s public-market shape but
        **deliberately NOT cached** — mark price is live market data, not
        deterministic metadata (ADR-0013). Public endpoint: NO sub-account
        validation (contrast :meth:`get_account_balance`).

        HTTP: GET /v5/market/tickers?category=linear&symbol=<symbol>.
        Response shape: ``result.list[0].markPrice`` (string). ``markPrice``
        is the liquidation/PnL reference (manipulation-resistant) — NOT
        ``lastPrice``/``indexPrice``. Decimal preserved per §5.3 (no float).

        Empty list response → :class:`OrderRejected` (instrument not found
        on exchange — delisted or typo'd symbol; mirror
        :meth:`get_instrument_info`).
        """
        await self._limiter.acquire(self._sub_account, "market")
        try:
            result = await self._client.request(
                "GET",
                "/v5/market/tickers",
                params={"category": _CATEGORY, "symbol": symbol},
                retries=3,
            )
        except RateLimitError:
            await self._on_rate_limit_hit("market")
            raise
        items = result.get("list", [])
        if not items:
            msg = f"instrument not found on exchange: {symbol}"
            raise OrderRejected(msg)
        return Decimal(str(items[0]["markPrice"]))

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
