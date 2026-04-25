"""Unit tests for :class:`packages.market.subscription.SubscriptionManager`.

Coverage matrix:

* **Symbol shape validation** — uppercase / ASCII / alnum gating; the
  rejection path in :func:`_validate_symbol`.
* **Refcount basics** — single subscriber 0→1→0 transitions; the
  underlying :class:`BinanceWsClient` sees one ``add_stream`` per
  configured kind on first acquire and one ``remove_stream`` per
  kind on last release.
* **H-014 hazard test** — ``test_refcount_sub_survives_one_caller_releasing``
  shadows the §20 hazard test name verbatim. Two callers acquire
  the same symbol; one releases; the survivor still receives
  dispatched frames and the underlying WS streams are intact.
* **Frame dispatch fan-out** — every active subscriber for a symbol
  receives every dispatched frame; subscribers for other symbols
  do not. Subscribe-ack frames (``{"result": null, "id": N}``) and
  malformed envelopes are ignored.
* **Race coverage** — concurrent ``__aenter__`` on the same symbol
  via :func:`asyncio.gather` issues exactly one ``add_stream`` per
  kind; staggered enter/exit interleaved with dispatch never sees
  ``remove_stream`` while count > 0.
* **Stream-pair handling** — both ``kline_1m`` and ``bookTicker``
  frames for a subscribed symbol land in the same feed (the §9.2
  line 1455 pair). ``stream_kinds`` constructor override reduces
  the pair to a singleton.
* **EOF semantics** — release enqueues a sentinel that surfaces as
  :class:`StopAsyncIteration` in the consumer's ``async for``.
* **Overflow drop-oldest + counter** — exceeding ``feed_maxsize``
  drops the oldest frame, increments the injected counter (when
  present), and emits a ``subscription_feed_overflow`` warning;
  ``overflow_counter=None`` is a no-op for the counter side.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
import structlog

from packages.market import (
    BinanceWsClient,
    SubscriptionManager,
    SymbolFeed,
)
from packages.market.subscription import _validate_symbol

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWsClient:
    """Minimal stand-in for :class:`BinanceWsClient`.

    Tracks the order and arguments of ``add_stream`` / ``remove_stream``
    calls and exposes the resulting stream set so tests can assert
    refcount-driven WS mutations directly.
    """

    def __init__(self) -> None:
        self._streams: set[str] = set()
        self.add_calls: list[str] = []
        self.remove_calls: list[str] = []

    @property
    def streams(self) -> frozenset[str]:
        return frozenset(self._streams)

    async def add_stream(self, stream: str) -> None:
        self.add_calls.append(stream)
        self._streams.add(stream)

    async def remove_stream(self, stream: str) -> None:
        self.remove_calls.append(stream)
        self._streams.discard(stream)


class _FakeCounter:
    """Stand-in for ``prometheus_client.Counter`` with ``.labels(...).inc()``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def labels(self, **labels: str) -> _FakeCounter:
        self._pending = labels
        return self

    def inc(self) -> None:
        self.calls.append(self._pending)


def _logger() -> Any:
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    log = logging.getLogger("test_subscription_manager")
    log.handlers = []
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return structlog.stdlib.get_logger("test_subscription_manager")


def _build_manager(
    *,
    stream_kinds: tuple[str, ...] = ("kline_1m", "bookTicker"),
    feed_maxsize: int = 1024,
    overflow_counter: _FakeCounter | None = None,
) -> tuple[SubscriptionManager, _FakeWsClient]:
    ws = _FakeWsClient()
    # SubscriptionManager declares ``ws: BinanceWsClient``; the fake
    # is structurally compatible (add_stream / remove_stream / streams).
    # Cast through Any to avoid an unrelated mypy nominal-typing
    # complaint at construction.
    mgr = SubscriptionManager(
        ws=ws,  # type: ignore[arg-type]
        logger=_logger(),
        stream_kinds=stream_kinds,
        feed_maxsize=feed_maxsize,
        overflow_counter=overflow_counter,  # type: ignore[arg-type]
    )
    return mgr, ws


def _frame(symbol: str, kind: str, payload: Any | None = None) -> dict[str, Any]:
    """Build a Binance multiplex envelope for ``symbol@kind``."""
    return {
        "stream": f"{symbol.lower()}@{kind}",
        "data": payload if payload is not None else {"k": kind},
    }


# ---------------------------------------------------------------------------
# Symbol shape validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_symbol",
    [
        "btcusdt",  # lowercase
        "BTC.P",  # non-alnum
        "BTC-USDT",  # non-alnum
        "",  # empty
        "BTC USDT",  # whitespace
        "BTCUSDT€",  # non-ASCII
    ],
)
def test_validate_symbol_rejects_malformed(bad_symbol: str) -> None:
    with pytest.raises(ValueError, match="uppercase ASCII alphanumeric"):
        _validate_symbol(bad_symbol)


@pytest.mark.parametrize("good_symbol", ["BTCUSDT", "ETHUSDT", "1000PEPEUSDT", "1INCHUSDT"])
def test_validate_symbol_accepts_canonical(good_symbol: str) -> None:
    _validate_symbol(good_symbol)  # no raise


def test_subscribe_validates_symbol_at_call_site() -> None:
    """``subscribe()`` raises synchronously — no need to ``async with``."""
    mgr, _ws = _build_manager()
    with pytest.raises(ValueError, match="uppercase ASCII alphanumeric"):
        mgr.subscribe("btcusdt")


# ---------------------------------------------------------------------------
# Refcount basics
# ---------------------------------------------------------------------------


async def test_first_subscribe_adds_one_stream_per_kind() -> None:
    """0→1 transition: one ``add_stream`` per configured kind, in order."""
    mgr, ws = _build_manager()
    async with mgr.subscribe("BTCUSDT"):
        assert ws.add_calls == ["btcusdt@kline_1m", "btcusdt@bookTicker"]
        assert ws.streams == frozenset({"btcusdt@kline_1m", "btcusdt@bookTicker"})


async def test_last_release_removes_all_streams() -> None:
    """1→0 transition: one ``remove_stream`` per kind on context exit."""
    mgr, ws = _build_manager()
    async with mgr.subscribe("BTCUSDT"):
        pass
    assert ws.remove_calls == ["btcusdt@kline_1m", "btcusdt@bookTicker"]
    assert ws.streams == frozenset()


async def test_second_subscribe_does_not_re_add_streams() -> None:
    """1→2 transition: refcount increments, no new ``add_stream``."""
    mgr, ws = _build_manager()
    async with mgr.subscribe("BTCUSDT"), mgr.subscribe("BTCUSDT"):
        assert ws.add_calls == ["btcusdt@kline_1m", "btcusdt@bookTicker"]


async def test_intermediate_release_keeps_streams() -> None:
    """2→1 transition: refcount decrements, no ``remove_stream``."""
    mgr, ws = _build_manager()
    async with mgr.subscribe("BTCUSDT"):
        sub_b = mgr.subscribe("BTCUSDT")
        await sub_b.__aenter__()
        await sub_b.__aexit__(None, None, None)
        assert ws.remove_calls == []
        assert ws.streams == frozenset({"btcusdt@kline_1m", "btcusdt@bookTicker"})


async def test_distinct_symbols_have_independent_refcounts() -> None:
    mgr, ws = _build_manager()
    async with mgr.subscribe("BTCUSDT"):
        async with mgr.subscribe("ETHUSDT"):
            assert ws.streams == frozenset(
                {
                    "btcusdt@kline_1m",
                    "btcusdt@bookTicker",
                    "ethusdt@kline_1m",
                    "ethusdt@bookTicker",
                }
            )
        # ETH released; BTC still held
        assert ws.streams == frozenset({"btcusdt@kline_1m", "btcusdt@bookTicker"})


# ---------------------------------------------------------------------------
# H-014 hazard test (§20)
# ---------------------------------------------------------------------------


async def test_refcount_sub_survives_one_caller_releasing() -> None:
    """H-014 hazard test (§20).

    Two callers `subscribe(BTCUSDT)`. Caller A exits its `async with`.
    Caller B's feed must still receive a subsequently-dispatched frame
    AND the underlying WS streams must remain — proving that the
    first-to-finish did not collapse the shared subscription.
    """
    mgr, ws = _build_manager()

    async with mgr.subscribe("BTCUSDT") as feed_b:
        sub_a = mgr.subscribe("BTCUSDT")
        feed_a = await sub_a.__aenter__()

        # Release A while B is still active.
        await sub_a.__aexit__(None, None, None)

        # WS streams still in place (1→0 has NOT fired).
        assert ws.streams == frozenset({"btcusdt@kline_1m", "btcusdt@bookTicker"})
        assert ws.remove_calls == []

        # Feed A is closed — `async for` on it sees StopAsyncIteration.
        with pytest.raises(StopAsyncIteration):
            await feed_a.__anext__()

        # Dispatch a frame; only B receives it.
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m"))
        received = await feed_b.__anext__()
        assert received["stream"] == "btcusdt@kline_1m"

    # B exited → 1→0 fires → streams removed.
    assert ws.streams == frozenset()
    assert ws.remove_calls == ["btcusdt@kline_1m", "btcusdt@bookTicker"]


# ---------------------------------------------------------------------------
# Frame dispatch fan-out
# ---------------------------------------------------------------------------


async def test_dispatch_fans_out_to_all_subscribers_of_symbol() -> None:
    mgr, _ws = _build_manager()
    async with mgr.subscribe("BTCUSDT") as feed_a, mgr.subscribe("BTCUSDT") as feed_b:
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"x": 1}))
        a = await feed_a.__anext__()
        b = await feed_b.__anext__()
        assert a == b
        assert a["data"] == {"x": 1}


async def test_dispatch_does_not_leak_across_symbols() -> None:
    mgr, _ws = _build_manager()
    async with mgr.subscribe("BTCUSDT") as feed_btc, mgr.subscribe("ETHUSDT") as feed_eth:
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"x": 1}))
        btc = await feed_btc.__anext__()
        assert btc["data"] == {"x": 1}
        # ETH feed should not have anything queued.
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.05):
                await feed_eth.__anext__()


@pytest.mark.parametrize(
    "frame",
    [
        {"result": None, "id": 1},  # subscribe-ack
        {"stream": "no-at-sign", "data": {}},  # malformed stream
        {"data": {"x": 1}},  # missing stream
        {"stream": 12345, "data": {}},  # non-string stream
    ],
)
async def test_dispatch_ignores_non_multiplex_frames(frame: dict[str, Any]) -> None:
    mgr, _ws = _build_manager()
    async with mgr.subscribe("BTCUSDT") as feed:
        await mgr.dispatch(frame)
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0.05):
                await feed.__anext__()


# ---------------------------------------------------------------------------
# Stream-pair handling
# ---------------------------------------------------------------------------


async def test_kline_and_bookticker_both_land_in_feed() -> None:
    """The §9.2 line 1455 pair: both stream kinds for a symbol fan into one feed."""
    mgr, _ws = _build_manager()
    async with mgr.subscribe("BTCUSDT") as feed:
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"k": "kline"}))
        await mgr.dispatch(_frame("BTCUSDT", "bookTicker", {"k": "ticker"}))
        first = await feed.__anext__()
        second = await feed.__anext__()
        assert {first["stream"], second["stream"]} == {
            "btcusdt@kline_1m",
            "btcusdt@bookTicker",
        }


async def test_stream_kinds_override_reduces_pair_to_singleton() -> None:
    """Constructor `stream_kinds=("kline_1m",)` skips the bookTicker leg."""
    mgr, ws = _build_manager(stream_kinds=("kline_1m",))
    async with mgr.subscribe("BTCUSDT"):
        assert ws.add_calls == ["btcusdt@kline_1m"]
        assert ws.streams == frozenset({"btcusdt@kline_1m"})


# ---------------------------------------------------------------------------
# Race coverage
# ---------------------------------------------------------------------------


async def test_concurrent_subscribers_share_one_underlying_subscribe() -> None:
    """`asyncio.gather` of N enters on the same symbol → one add_stream per kind."""
    mgr, ws = _build_manager()
    subs = [mgr.subscribe("BTCUSDT") for _ in range(10)]
    feeds = await asyncio.gather(*[s.__aenter__() for s in subs])

    # Exactly one add per kind, regardless of N.
    assert ws.add_calls == ["btcusdt@kline_1m", "btcusdt@bookTicker"]

    # All feeds receive the same dispatch.
    await mgr.dispatch(_frame("BTCUSDT", "kline_1m"))
    received = await asyncio.gather(*[f.__anext__() for f in feeds])
    assert all(r["stream"] == "btcusdt@kline_1m" for r in received)

    # Tear down concurrently; only one set of remove_stream calls.
    await asyncio.gather(*[s.__aexit__(None, None, None) for s in subs])
    assert ws.remove_calls == ["btcusdt@kline_1m", "btcusdt@bookTicker"]


async def test_release_during_dispatch_does_not_drop_active_subscribers() -> None:
    """Lock serialises release vs dispatch: in-flight dispatch never sees a half-state."""
    mgr, _ws = _build_manager()
    sub_a = mgr.subscribe("BTCUSDT")
    sub_b = mgr.subscribe("BTCUSDT")
    feed_a = await sub_a.__aenter__()
    feed_b = await sub_b.__aenter__()

    # Interleave: release A and dispatch a frame in the same scheduler tick.
    await asyncio.gather(
        sub_a.__aexit__(None, None, None),
        mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"x": 7})),
    )

    # B must have received the frame regardless of interleave order.
    received = await feed_b.__anext__()
    assert received["data"] == {"x": 7}

    # A's feed is EOF (released).
    with pytest.raises(StopAsyncIteration):
        await feed_a.__anext__()

    await sub_b.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# EOF semantics
# ---------------------------------------------------------------------------


async def test_release_signals_eof_to_pending_anext() -> None:
    """A consumer parked in ``__anext__`` wakes up with ``StopAsyncIteration`` on release."""
    mgr, _ws = _build_manager()
    sub = mgr.subscribe("BTCUSDT")
    feed = await sub.__aenter__()

    # Park a consumer on __anext__ before the release.
    consumer_task: asyncio.Task[dict[str, Any]] = asyncio.create_task(feed.__anext__())
    # Give the task a tick to enter the queue's wait.
    await asyncio.sleep(0)

    await sub.__aexit__(None, None, None)

    with pytest.raises(StopAsyncIteration):
        await consumer_task


async def test_async_for_exits_cleanly_on_release() -> None:
    """``async for frame in feed:`` terminates when the parent ``async with`` exits."""
    mgr, _ws = _build_manager()
    received: list[dict[str, Any]] = []

    async def consume(feed: SymbolFeed) -> None:
        async for frame in feed:
            received.append(frame)

    sub = mgr.subscribe("BTCUSDT")
    feed = await sub.__aenter__()
    consumer_task = asyncio.create_task(consume(feed))

    await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"i": 0}))
    await asyncio.sleep(0)
    await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"i": 1}))
    await asyncio.sleep(0)

    await sub.__aexit__(None, None, None)
    await consumer_task

    assert [f["data"]["i"] for f in received] == [0, 1]


# ---------------------------------------------------------------------------
# Overflow drop-oldest + counter
# ---------------------------------------------------------------------------


async def test_overflow_drops_oldest_and_increments_counter() -> None:
    counter = _FakeCounter()
    mgr, _ws = _build_manager(feed_maxsize=2, overflow_counter=counter)
    async with mgr.subscribe("BTCUSDT") as feed:
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"i": 0}))
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"i": 1}))
        # Third dispatch overflows; oldest ({"i": 0}) is dropped.
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"i": 2}))

        first = await feed.__anext__()
        second = await feed.__anext__()
        assert [first["data"]["i"], second["data"]["i"]] == [1, 2]

    assert counter.calls == [{"symbol": "BTCUSDT"}]


async def test_overflow_without_counter_is_noop_path() -> None:
    """`overflow_counter=None` (default for tests with no metric injection): no raise."""
    mgr, _ws = _build_manager(feed_maxsize=1, overflow_counter=None)
    async with mgr.subscribe("BTCUSDT") as feed:
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"i": 0}))
        await mgr.dispatch(_frame("BTCUSDT", "kline_1m", {"i": 1}))
        # Should still get the freshest frame, no exception.
        latest = await feed.__anext__()
        assert latest["data"] == {"i": 1}


# ---------------------------------------------------------------------------
# BinanceWsClient surface compatibility (structural smoke)
# ---------------------------------------------------------------------------


def test_binance_ws_client_satisfies_subscription_manager_surface() -> None:
    """The real :class:`BinanceWsClient` exposes ``add_stream`` /
    ``remove_stream`` so SubscriptionManager can drive it without
    adapter glue. Structural smoke — confirms the T-101b → T-102
    seam stays intact across future refactors of either side.
    """
    assert hasattr(BinanceWsClient, "add_stream")
    assert hasattr(BinanceWsClient, "remove_stream")
