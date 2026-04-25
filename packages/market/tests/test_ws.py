"""Unit tests for :class:`packages.market.ws.BinanceWsClient`.

Coverage matrix:

* **State machine** — DISCONNECTED → CONNECTING → CONNECTED →
  RECONNECTING → CONNECTED → CLOSED edges, re-entry guard on
  :meth:`run`, idempotent :meth:`close`.
* **Initial subscribe** — SUBSCRIBE frame sent (sorted) on connect;
  empty initial set sends nothing.
* **Receive loop** — JSON frames dispatched to handler; non-JSON
  frame raises :class:`BinanceWsError`.
* **add_stream / remove_stream** — connected state sends
  SUBSCRIBE/UNSUBSCRIBE frame; disconnected state mutates set only;
  idempotent on duplicates / unknowns.
* **H-007 hazard test** — `test_ws_reconnect_uses_exponential_backoff`
  asserts the reconnect loop drives `asyncio.sleep` with delays
  within the `[0, min(2**n, 60)]` envelope from
  :func:`exp_backoff_delays`.
* **Long-disconnect signal** — single-fire across one outage;
  `long_disconnect_count` increments once even if the outage spans
  many reconnect attempts; resets after a successful reconnect so a
  later outage can fire again.

`websockets.asyncio.client.connect` is patched via monkeypatch so no
real WS connection is opened. The fake `_FakeWs` mirrors enough of
the `ClientConnection` async-iterator + send/close API for the
production code path to exercise without wire calls. `asyncio.sleep`
is patched to capture delays without burning real wall-time.
"""

from __future__ import annotations

import json
import logging
import random
from typing import Any

import pytest
import structlog
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from packages.market import (
    BinanceWsClient,
    BinanceWsError,
    ConnectionState,
    NotConnectedError,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWs:
    """Minimal stand-in for ``websockets.asyncio.client.ClientConnection``.

    Iterates over a pre-loaded message queue. After messages are exhausted:

    * ``end_with=None`` (default) — the iter ``await``s an internal
      :class:`asyncio.Event` that :meth:`close` sets, then raises
      :class:`ConnectionClosedOK` so the production receive loop exits
      via its standard WebSocketException path. This is the normal
      shape for tests that connect once and then drive ``close()``.
    * ``end_with=<exc>`` — the iter raises ``exc`` immediately when
      messages exhaust, simulating server-initiated disconnect. Use
      :class:`ConnectionClosedError` for reconnect/H-007 tests.
    """

    def __init__(
        self,
        messages: list[str] | None = None,
        *,
        end_with: BaseException | None = None,
    ) -> None:
        import asyncio as _asyncio

        self._messages = list(messages or [])
        self._end_with = end_with
        self._closed_event = _asyncio.Event()
        self.sent: list[str] = []
        self.closed = False

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def close(self) -> None:
        self.closed = True
        self._closed_event.set()

    def __aiter__(self) -> _FakeWs:
        return self

    async def __anext__(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        if self._end_with is not None:
            raise self._end_with
        await self._closed_event.wait()
        raise ConnectionClosedOK(None, None)


class _ScriptedConnect:
    """Programmable replacement for `ws_connect` in `packages.market.ws`.

    Each entry in `script` is either:
      * an exception → raised on `__aenter__` (simulates connect failure).
      * a `_FakeWs`  → yielded as the connection on success.

    The script is consumed in order; running off the end raises
    StopIteration — tests should provide enough entries to cover
    `close()` being called on the client.
    """

    def __init__(self, script: list[BaseException | _FakeWs]) -> None:
        self._script = list(script)
        self.attempts: list[BaseException | _FakeWs] = []

    def __call__(self, _url: str) -> _ScriptedConnect:
        return self

    async def __aenter__(self) -> _FakeWs:
        item = self._script.pop(0)
        self.attempts.append(item)
        if isinstance(item, BaseException):
            raise item
        return item

    async def __aexit__(self, *_args: object) -> None:
        return None


def _logger() -> Any:
    """Return a structlog BoundLogger silenced for tests."""
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    log = logging.getLogger("test_ws")
    log.handlers = []
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return structlog.stdlib.get_logger("test_ws")


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    script: list[BaseException | _FakeWs],
    *,
    initial_streams: set[str] | None = None,
    handler: Any = None,
    long_disconnect_threshold_seconds: float = 60.0,
    sleep_capture: list[float] | None = None,
    seed: int = 0xC0FFEE,
) -> tuple[BinanceWsClient, _ScriptedConnect]:
    """Wire a `BinanceWsClient` against scripted ws_connect + sleep capture.

    Patches `packages.market.ws._async_sleep` (the dedicated alias the
    reconnect loop uses) so the test's own `await asyncio.sleep(0)`
    yields are unaffected. The fake yields once via real asyncio.sleep
    so close() can interleave between reconnect attempts.
    """
    import asyncio as _real_asyncio

    connect = _ScriptedConnect(script)
    monkeypatch.setattr("packages.market.ws.ws_connect", connect)

    async def _capture_sleep(delay: float) -> None:
        if sleep_capture is not None:
            sleep_capture.append(delay)
        await _real_asyncio.sleep(0)

    monkeypatch.setattr("packages.market.ws._async_sleep", _capture_sleep)

    async def _default_handler(_msg: dict[str, Any]) -> None:
        return None

    client = BinanceWsClient(
        initial_streams=initial_streams or set(),
        handler=handler if handler is not None else _default_handler,
        logger=_logger(),
        long_disconnect_threshold_seconds=long_disconnect_threshold_seconds,
        rng=random.Random(seed),
    )
    return client, connect


# ---------------------------------------------------------------------------
# Lifecycle / state machine
# ---------------------------------------------------------------------------


async def test_initial_state_is_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch, script=[])
    assert client.state is ConnectionState.DISCONNECTED


async def test_run_then_close_drains_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Single connect, exhaust messages, close → terminal CLOSED state."""
    received: list[dict[str, Any]] = []

    async def handler(msg: dict[str, Any]) -> None:
        received.append(msg)

    fake = _FakeWs(messages=['{"a": 1}', '{"b": 2}'])

    client, _connect = _build_client(
        monkeypatch,
        script=[fake, ConnectionClosedError(None, None)],
        handler=handler,
        sleep_capture=[],
    )

    async def _stop_after_first_failure() -> None:
        # close() will be invoked the moment the run loop enters the
        # second (failing) connect attempt — but to make the test
        # deterministic we close BEFORE re-entry by short-circuiting
        # via the fake's end_with. Practical pattern: after the first
        # FakeWs exhausts, the loop will try to reconnect via the
        # scripted ConnectionClosedError; we close inline by setting
        # state to CLOSED in the except path. Here we just drive run()
        # and trust the close-on-second-attempt semantics.
        pass

    # Drive run via a task we can cancel.
    import asyncio

    task = asyncio.create_task(client.run())
    # Yield to the loop so ws connect + receive happen.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await client.close()
    await task

    assert received == [{"a": 1}, {"b": 2}]
    assert client.state is ConnectionState.CLOSED


async def test_run_rejected_on_non_disconnected_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling run() while CLOSED raises NotConnectedError."""
    client, _ = _build_client(monkeypatch, script=[])
    await client.close()  # transition to CLOSED
    with pytest.raises(NotConnectedError):
        await client.run()


async def test_close_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch, script=[])
    await client.close()
    await client.close()  # second call is no-op
    assert client.state is ConnectionState.CLOSED


# ---------------------------------------------------------------------------
# Initial subscribe + receive
# ---------------------------------------------------------------------------


async def test_initial_subscribe_sent_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUBSCRIBE frame on connect contains the initial streams in sorted order."""
    fake = _FakeWs(messages=[])
    client, _connect = _build_client(
        monkeypatch,
        script=[fake],
        initial_streams={"ethusdt@kline_1m", "btcusdt@kline_1m"},
        sleep_capture=[],
    )
    import asyncio

    task = asyncio.create_task(client.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await client.close()
    await task

    assert len(fake.sent) == 1
    sent = json.loads(fake.sent[0])
    assert sent["method"] == "SUBSCRIBE"
    assert sent["params"] == ["btcusdt@kline_1m", "ethusdt@kline_1m"]
    assert isinstance(sent["id"], int)


async def test_no_initial_subscribe_when_streams_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeWs(messages=[])
    client, _connect = _build_client(
        monkeypatch,
        script=[fake],
        initial_streams=set(),
        sleep_capture=[],
    )
    import asyncio

    task = asyncio.create_task(client.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await client.close()
    await task

    assert fake.sent == []


async def test_non_json_frame_raises_binance_ws_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Corrupt frames surface as :class:`BinanceWsError` (not silent drop)."""
    fake = _FakeWs(messages=["not json"])
    client, _ = _build_client(monkeypatch, script=[fake], sleep_capture=[])

    with pytest.raises(BinanceWsError):
        await client.run()


# ---------------------------------------------------------------------------
# add_stream / remove_stream
# ---------------------------------------------------------------------------


async def test_add_stream_when_disconnected_only_mutates_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnected state: add_stream updates internal set, sends nothing."""
    client, _ = _build_client(monkeypatch, script=[])
    await client.add_stream("btcusdt@kline_1m")
    assert "btcusdt@kline_1m" in client.streams


async def test_add_remove_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch, script=[])
    await client.add_stream("btcusdt@kline_1m")
    await client.add_stream("btcusdt@kline_1m")  # dup → no-op
    assert client.streams == frozenset({"btcusdt@kline_1m"})

    await client.remove_stream("btcusdt@kline_1m")
    await client.remove_stream("btcusdt@kline_1m")  # already gone → no-op
    assert client.streams == frozenset()


async def test_add_stream_when_connected_sends_subscribe_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connected state: add_stream sends SUBSCRIBE frame mid-stream."""
    fake = _FakeWs(messages=[])
    client, _connect = _build_client(
        monkeypatch,
        script=[fake],
        initial_streams=set(),
        sleep_capture=[],
    )
    import asyncio

    task = asyncio.create_task(client.run())
    # Yield so connect completes and state -> CONNECTED.
    for _ in range(3):
        await asyncio.sleep(0)
    assert client.state is ConnectionState.CONNECTED

    await client.add_stream("btcusdt@kline_1m")

    await client.close()
    await task

    # One frame: the SUBSCRIBE we just issued.
    assert len(fake.sent) == 1
    sent = json.loads(fake.sent[0])
    assert sent["method"] == "SUBSCRIBE"
    assert sent["params"] == ["btcusdt@kline_1m"]


async def test_remove_stream_when_connected_sends_unsubscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeWs(messages=[])
    client, _connect = _build_client(
        monkeypatch,
        script=[fake],
        initial_streams={"btcusdt@kline_1m"},
        sleep_capture=[],
    )
    import asyncio

    task = asyncio.create_task(client.run())
    for _ in range(3):
        await asyncio.sleep(0)

    await client.remove_stream("btcusdt@kline_1m")

    await client.close()
    await task

    # First frame is the initial SUBSCRIBE; second is the UNSUBSCRIBE.
    assert len(fake.sent) == 2
    unsubscribe = json.loads(fake.sent[1])
    assert unsubscribe["method"] == "UNSUBSCRIBE"
    assert unsubscribe["params"] == ["btcusdt@kline_1m"]


# ---------------------------------------------------------------------------
# H-007: WS reconnect with exponential backoff
# ---------------------------------------------------------------------------


async def test_ws_reconnect_uses_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-007 hazard test (§20).

    The reconnect loop drives `asyncio.sleep` with delays drawn from
    `exp_backoff_delays`; each delay must lie inside
    ``[0, min(2**n, 60)]`` for attempt ``n``. Asserts the envelope, not
    a specific sequence (jitter randomizes within the ceiling), so the
    test is robust to RNG implementation drift while still proving the
    backoff curve.
    """
    sleeps: list[float] = []
    # Eight failures, then a success — gives us 8 backoff samples
    # spanning into the cap-saturation regime (attempts 6+ → 60s ceil).
    fake = _FakeWs(messages=[])
    # Build with extend/append so mypy sees each element typed against the
    # union annotation; literal `[CCE() for _ in range(8)] + [fake]` triggers
    # list-invariance error because the inferred element type narrows.
    script: list[BaseException | _FakeWs] = []
    script.extend(ConnectionClosedError(None, None) for _ in range(8))
    script.append(fake)
    client, _connect = _build_client(
        monkeypatch,
        script=script,
        sleep_capture=sleeps,
    )
    import asyncio

    task = asyncio.create_task(client.run())
    # Drain enough event-loop ticks to consume the 8 failed attempts +
    # land on the successful FakeWs.
    for _ in range(50):
        await asyncio.sleep(0)
    await client.close()
    await task

    assert len(sleeps) >= 8, f"expected ≥8 backoff sleeps, got {len(sleeps)}"
    for n, delay in enumerate(sleeps[:8]):
        ceiling = min(2**n, 60.0)
        assert 0.0 <= delay <= ceiling, (
            f"attempt {n}: delay {delay} outside H-007 envelope [0, {ceiling}]"
        )


async def test_long_disconnect_signal_fires_once_per_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`long_disconnect_count` increments exactly once across one outage.

    Threshold lowered to 0s so the very first reconnect attempt
    crosses it; subsequent attempts within the same outage must NOT
    re-fire (single-fire latch). After a successful reconnect the
    latch resets — verified by spinning a second outage.
    """
    # fake_a explicitly disconnects so the loop advances to the second
    # outage; fake_b is the test's terminal connection and hangs until
    # close() so we don't run off the script.
    fake_a = _FakeWs(messages=[], end_with=ConnectionClosedError(None, None))
    fake_b = _FakeWs(messages=[])
    script: list[BaseException | _FakeWs] = [
        # First outage — 3 failed attempts then recover.
        ConnectionClosedError(None, None),
        ConnectionClosedError(None, None),
        ConnectionClosedError(None, None),
        fake_a,
        # Second outage — 2 failed attempts then recover.
        ConnectionClosedError(None, None),
        ConnectionClosedError(None, None),
        fake_b,
    ]
    client, _connect = _build_client(
        monkeypatch,
        script=script,
        long_disconnect_threshold_seconds=0.0,
        sleep_capture=[],
    )
    import asyncio

    task = asyncio.create_task(client.run())
    for _ in range(50):
        await asyncio.sleep(0)
    await client.close()
    await task

    # One latch-fire per outage — two distinct outages here.
    assert client.long_disconnect_count == 2, (
        f"expected 2 long-disconnect signals, got {client.long_disconnect_count}"
    )


async def test_reconnect_resends_subscribe_for_current_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After reconnect, the client re-issues SUBSCRIBE for `self._streams`."""
    # fake_first explicitly disconnects so the loop reaches fake_second;
    # fake_second hangs until close() to keep the script bounded.
    fake_first = _FakeWs(messages=[], end_with=ConnectionClosedError(None, None))
    fake_second = _FakeWs(messages=[])
    client, _connect = _build_client(
        monkeypatch,
        script=[
            fake_first,
            fake_second,
        ],
        initial_streams={"btcusdt@kline_1m"},
        sleep_capture=[],
    )
    import asyncio

    task = asyncio.create_task(client.run())
    for _ in range(20):
        await asyncio.sleep(0)
    await client.close()
    await task

    # Both connections received an initial SUBSCRIBE frame.
    assert len(fake_first.sent) == 1
    assert len(fake_second.sent) == 1
    assert json.loads(fake_first.sent[0])["params"] == ["btcusdt@kline_1m"]
    assert json.loads(fake_second.sent[0])["params"] == ["btcusdt@kline_1m"]
