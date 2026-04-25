"""Reference-counted Binance subscription manager (§9.2, H-014).

:class:`SubscriptionManager` layers per-symbol reference counting on
top of :meth:`BinanceWsClient.add_stream` / :meth:`remove_stream`
(T-101b). Multiple callers may subscribe to the same symbol
concurrently; the underlying WS subscription is established on the
0→1 transition and torn down on the 1→0 transition. v2 equivalent of
the v1 ``PriceManager`` refcount discipline (``scalper/bot/price_ws.py``),
promoted from a private dict to a public context-manager API per H-014.

Public surface:

* :meth:`SubscriptionManager.subscribe` — async-CM whose
  ``__aenter__`` yields a :class:`SymbolFeed` async iterator. The
  feed yields every Binance multiplex envelope
  (``{"stream": "...", "data": {...}}``) for the subscribed symbol
  until the caller exits the ``async with`` block.
* :meth:`SubscriptionManager.dispatch` — handler the service
  composition root wires into ``BinanceWsClient(handler=…)``. Routes
  each incoming frame to every active feed for the matching symbol.

H-014 contract: when caller A holds ``async with subscribe("BTCUSDT")``
and caller B does the same, A exiting its block does **not** cancel
B's feed; B continues receiving frames until it too exits. Hazard
test: ``test_refcount_sub_survives_one_caller_releasing``.

Symbol vocabulary: callers pass canonical Bybit-shape symbols
(e.g., ``"BTCUSDT"``); SubscriptionManager translates to Binance
stream names internally (``"btcusdt@kline_1m"`` /
``"btcusdt@bookTicker"``). The kline_1m + bookTicker pair mirrors
the §9.2 line 1455 spec for an "active symbol"; ``stream_kinds`` is
constructor-configurable for future scenarios that need a subset.
``subscribe`` validates the symbol shape synchronously so a
malformed call surfaces at the call site rather than producing a
malformed Binance stream name that Binance silently ignores.

Queue policy (single-module decision; no ADR per §0.6 — sensible
default + escape hatch via ``feed_maxsize``): each feed has a
bounded :class:`asyncio.Queue` (default ``maxsize=1024``). On
overflow the oldest queued frame is dropped (FIFO eviction) so a
slow consumer never back-pressures the WS receive loop and never
freezes other consumers' feeds. Each drop increments
``subscription_feed_overflow_total{symbol}`` (when the optional
counter is injected) and emits a ``subscription_feed_overflow``
warning log. T-104 (OHLC pipeline) treats the contract as "may drop
intermediate frames under back-pressure"; closed-bucket detection
rides on the next in-progress frame's monotonic ``bucket_start``
regardless.

Reconnect interaction with T-101b: during a WS outage,
:class:`BinanceWsClient` is ``RECONNECTING`` and dispatches no
frames; feeds silently block on ``__anext__`` until reconnect
resumes traffic. No spurious EOF, no exception. Callers needing
liveness pair the ``async for`` with :func:`asyncio.timeout`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from types import TracebackType

    from prometheus_client import Counter
    from structlog.stdlib import BoundLogger

    from .ws import BinanceWsClient


__all__ = ["SubscriptionManager", "SymbolFeed"]


_DEFAULT_STREAM_KINDS: tuple[str, ...] = ("kline_1m", "bookTicker")
_DEFAULT_FEED_MAXSIZE: int = 1024


def _validate_symbol(symbol: str) -> None:
    """Reject malformed symbols before they form malformed stream names.

    Canonical Bybit/Binance derivative symbols are uppercase ASCII
    alphanumeric (BTCUSDT, ETHUSDT, …). A subscribe with a malformed
    string would otherwise produce a stream name like
    ``btc.p@kline_1m`` that Binance silently ignores — failing here
    surfaces the bug at the call site.
    """
    if not symbol.isascii() or not symbol.isalnum() or symbol.upper() != symbol:
        msg = f"symbol must be uppercase ASCII alphanumeric: {symbol!r}"
        raise ValueError(msg)


class _Sentinel:
    """End-of-feed marker enqueued on subscription release."""


_EOF = _Sentinel()


class SymbolFeed:
    """Per-subscriber async iterator of Binance frames for one symbol.

    Iteration semantics:

    * ``async for frame in feed:`` yields every multiplex envelope
      (``{"stream": "...", "data": {...}}``) the WS dispatched for
      this symbol while the parent ``async with subscribe(symbol)``
      block was open.
    * On the parent block exiting (this caller's release, regardless
      of whether other callers remain), iteration ends via
      :class:`StopAsyncIteration` — the standard ``async for`` exit,
      no distinct exception type. Callers must not catch
      :class:`packages.market.errors.BinanceWsError` expecting feed
      end; that exception signals a WS-layer failure, not normal
      shutdown.
    * Intermediate frames may be dropped if the consumer falls behind
      the queue's bounded ``maxsize`` (drop-oldest). Drops are
      counted via the manager's overflow counter; the iterator
      itself emits no signal on a drop.
    """

    def __init__(self, symbol: str, *, maxsize: int) -> None:
        self._symbol = symbol
        self._queue: asyncio.Queue[dict[str, Any] | _Sentinel] = asyncio.Queue(maxsize=maxsize)

    @property
    def symbol(self) -> str:
        return self._symbol

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> dict[str, Any]:
        item = await self._queue.get()
        if isinstance(item, _Sentinel):
            raise StopAsyncIteration
        return item

    def _put(self, frame: dict[str, Any]) -> bool:
        """Enqueue ``frame``; on overflow drop oldest. Returns True on drop."""
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(frame)
            return True
        return False

    def _signal_eof(self) -> None:
        """Enqueue end-of-feed sentinel; drops oldest if queue is full."""
        try:
            self._queue.put_nowait(_EOF)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(_EOF)


class _Subscription:
    """Async-CM returned by :meth:`SubscriptionManager.subscribe`.

    Holds one ref on the symbol while the ``async with`` block is
    open. ``__aenter__`` returns the per-call :class:`SymbolFeed`;
    ``__aexit__`` releases the ref (decrements count, signals EOF on
    this feed, and on a 1→0 transition tears down the underlying WS
    streams).
    """

    def __init__(self, manager: SubscriptionManager, symbol: str) -> None:
        self._manager = manager
        self._symbol = symbol
        self._feed: SymbolFeed | None = None

    async def __aenter__(self) -> SymbolFeed:
        feed = await self._manager._acquire(self._symbol)
        self._feed = feed
        return feed

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        feed = self._feed
        self._feed = None
        if feed is None:
            return
        await self._manager._release(self._symbol, feed)


class SubscriptionManager:
    """Refcounted facade over :class:`BinanceWsClient` (H-014).

    See module docstring for the H-014 contract, queue policy, and
    reconnect interaction.
    """

    def __init__(
        self,
        ws: BinanceWsClient,
        logger: BoundLogger,
        *,
        stream_kinds: tuple[str, ...] = _DEFAULT_STREAM_KINDS,
        feed_maxsize: int = _DEFAULT_FEED_MAXSIZE,
        overflow_counter: Counter | None = None,
    ) -> None:
        self._ws = ws
        self._logger = logger
        self._stream_kinds = stream_kinds
        self._feed_maxsize = feed_maxsize
        self._overflow_counter = overflow_counter
        self._lock = asyncio.Lock()
        self._counts: dict[str, int] = {}
        self._feeds: dict[str, list[SymbolFeed]] = {}

    def subscribe(self, symbol: str) -> _Subscription:
        """Acquire a refcounted subscription for ``symbol``.

        Returns an async context manager whose ``__aenter__`` resolves
        to a :class:`SymbolFeed` async iterator. Validates ``symbol``
        shape synchronously so a malformed call fails at the call
        site rather than producing a malformed Binance stream name.
        """
        _validate_symbol(symbol)
        return _Subscription(self, symbol)

    async def dispatch(self, frame: dict[str, Any]) -> None:
        """Route one incoming Binance frame to every active feed for its symbol.

        Wired as the :class:`BinanceWsClient` ``handler`` by the
        composition root. Frame shape is the multiplex envelope
        ``{"stream": "<symbol>@<kind>", "data": {...}}``; the symbol
        is parsed back to canonical (uppercase) for routing. Frames
        whose ``stream`` field doesn't match the multiplex shape are
        silently ignored (e.g., subscribe-ack frames
        ``{"result": null, "id": N}``).

        Holds the manager lock through the per-feed enqueues so a
        concurrent release doesn't see a half-state. Enqueues are
        synchronous (``put_nowait``), so the lock is never held
        across an ``await``.
        """
        stream = frame.get("stream")
        if not isinstance(stream, str) or "@" not in stream:
            return
        symbol_lower, _, _ = stream.partition("@")
        symbol = symbol_lower.upper()
        async with self._lock:
            for feed in self._feeds.get(symbol, ()):
                if feed._put(frame):
                    if self._overflow_counter is not None:
                        self._overflow_counter.labels(symbol=symbol).inc()
                    self._logger.warning(
                        "subscription_feed_overflow",
                        symbol=symbol,
                        maxsize=self._feed_maxsize,
                    )

    async def _acquire(self, symbol: str) -> SymbolFeed:
        async with self._lock:
            count = self._counts.get(symbol, 0)
            self._counts[symbol] = count + 1
            feed = SymbolFeed(symbol, maxsize=self._feed_maxsize)
            self._feeds.setdefault(symbol, []).append(feed)
            if count == 0:
                for kind in self._stream_kinds:
                    await self._ws.add_stream(_stream_name(symbol, kind))
                self._logger.info(
                    "subscription_acquired_first",
                    symbol=symbol,
                    stream_kinds=list(self._stream_kinds),
                )
            return feed

    async def _release(self, symbol: str, feed: SymbolFeed) -> None:
        async with self._lock:
            count = self._counts.get(symbol, 0)
            if count == 0:
                return
            feeds = self._feeds.get(symbol, [])
            if feed in feeds:
                feeds.remove(feed)
            feed._signal_eof()
            if count > 1:
                self._counts[symbol] = count - 1
                return
            del self._counts[symbol]
            self._feeds.pop(symbol, None)
            for kind in self._stream_kinds:
                await self._ws.remove_stream(_stream_name(symbol, kind))
            self._logger.info("subscription_released_last", symbol=symbol)


def _stream_name(symbol: str, kind: str) -> str:
    return f"{symbol.lower()}@{kind}"
