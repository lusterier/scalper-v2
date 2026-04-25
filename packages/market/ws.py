"""Binance WebSocket client (§9.2, §3.1, H-007).

`BinanceWsClient` is the long-lived public-market-data WS connection
that ``market-data-svc`` (T-100/T-104) drives. Responsibilities:

* Open one WS to the `/stream` multiplex endpoint with an initial
  subscription set; receive messages and dispatch them to a caller-
  supplied async handler.
* On disconnect, reconnect with exponential backoff + full jitter
  per H-007 / §9.2 (1s → 60s, capped, jittered). Re-subscribe the
  current stream set on every successful reconnect.
* Track elapsed disconnect time across the backoff loop; emit a
  structured ``binance_ws_long_disconnect`` log + increment a counter
  attribute when the threshold (default 60s) is crossed. The actual
  alert delivery is the alerting-svc's job (F5); this client only
  emits the signal.
* `add_stream` / `remove_stream` mutate the subscription set
  optimistically — frames are sent only when the client is currently
  connected; otherwise the change takes effect on the next reconnect.
  No ack tracking — Binance's response (`{"result": null, "id": N}`)
  is informational; subscription liveness is observable via incoming
  data.

Refcounting is **not** in this layer. T-102 ``SubscriptionManager``
layers refcounts on top, calling ``add_stream``/``remove_stream``
only on 0↔1 transitions per H-014.
"""

from __future__ import annotations

import contextlib
import json
import time
from asyncio import sleep as _async_sleep
from enum import Enum
from typing import TYPE_CHECKING, Any

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from .backoff import exp_backoff_delays
from .errors import BinanceWsError, NotConnectedError

if TYPE_CHECKING:
    import random
    from collections.abc import Awaitable, Callable

    from structlog.stdlib import BoundLogger
    from websockets.asyncio.client import ClientConnection


__all__ = ["BinanceWsClient", "ConnectionState"]


_DEFAULT_URL = "wss://stream.binance.com:9443/stream"
_DEFAULT_LONG_DISCONNECT_SECONDS = 60.0


class ConnectionState(Enum):
    """Lifecycle states; mirrors :class:`packages.bus.client.ConnectionState`."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


class BinanceWsClient:
    """Async Binance multiplex-WS wrapper with H-007 reconnect."""

    def __init__(
        self,
        *,
        initial_streams: set[str],
        handler: Callable[[dict[str, Any]], Awaitable[None]],
        logger: BoundLogger,
        url: str = _DEFAULT_URL,
        long_disconnect_threshold_seconds: float = _DEFAULT_LONG_DISCONNECT_SECONDS,
        rng: random.Random | None = None,
    ) -> None:
        self._url = url
        self._streams: set[str] = set(initial_streams)
        self._handler = handler
        self._logger = logger
        self._long_disconnect_threshold = long_disconnect_threshold_seconds
        self._rng = rng
        self._state = ConnectionState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._next_id = 1
        self.long_disconnect_count: int = 0

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def streams(self) -> frozenset[str]:
        """Snapshot of currently-tracked subscriptions (frozen for callers)."""
        return frozenset(self._streams)

    async def run(self) -> None:
        """Drive the connect → receive → reconnect loop until :meth:`close`.

        Re-entry guard: only legal from :attr:`ConnectionState.DISCONNECTED`.
        """
        if self._state is not ConnectionState.DISCONNECTED:
            msg = f"run() called in state {self._state.value!r}; expected 'disconnected'"
            raise NotConnectedError(msg)
        await self._reconnect_loop()

    async def close(self) -> None:
        """Stop the receive/reconnect loop and close the underlying WS.

        Idempotent — calling on an already-closed client is a no-op.
        """
        if self._state is ConnectionState.CLOSED:
            return
        self._state = ConnectionState.CLOSED
        if self._ws is not None:
            with contextlib.suppress(WebSocketException):
                await self._ws.close()
        self._ws = None

    async def add_stream(self, stream: str) -> None:
        """Subscribe to ``stream`` (e.g. ``"btcusdt@kline_1m"``).

        Idempotent — second call for the same stream is a no-op.
        Sends a SUBSCRIBE frame immediately when connected; otherwise
        the change takes effect on the next reconnect.
        """
        if stream in self._streams:
            return
        self._streams.add(stream)
        if self._state is ConnectionState.CONNECTED and self._ws is not None:
            await self._send_subscription(method="SUBSCRIBE", params=[stream])

    async def remove_stream(self, stream: str) -> None:
        """Unsubscribe from ``stream``. No-op if not currently tracked."""
        if stream not in self._streams:
            return
        self._streams.discard(stream)
        if self._state is ConnectionState.CONNECTED and self._ws is not None:
            await self._send_subscription(method="UNSUBSCRIBE", params=[stream])

    async def _reconnect_loop(self) -> None:
        """Open WS, run receive loop, reconnect on disconnect with H-007 backoff.

        Tracks ``disconnect_started_at`` across the backoff window so a
        single long outage emits one ``binance_ws_long_disconnect`` signal
        even if it spans many reconnect attempts.
        """
        delays = exp_backoff_delays(rng=self._rng)
        disconnect_started_at: float | None = None
        long_disconnect_logged = False
        attempt = 0
        while self._state is not ConnectionState.CLOSED:
            self._state = (
                ConnectionState.CONNECTING
                if disconnect_started_at is None
                else ConnectionState.RECONNECTING
            )
            try:
                async with ws_connect(self._url) as ws:
                    self._ws = ws
                    self._state = ConnectionState.CONNECTED
                    if self._streams:
                        await self._send_subscription(
                            method="SUBSCRIBE", params=sorted(self._streams)
                        )
                    self._logger.info(
                        "binance_ws_connected",
                        url=self._url,
                        streams=len(self._streams),
                        attempt=attempt,
                    )
                    disconnect_started_at = None
                    long_disconnect_logged = False
                    attempt = 0
                    await self._receive_until_closed(ws)
            except (WebSocketException, OSError) as exc:
                if self._state is ConnectionState.CLOSED:
                    return
                self._ws = None
                if disconnect_started_at is None:
                    disconnect_started_at = time.monotonic()
                elapsed = time.monotonic() - disconnect_started_at
                if not long_disconnect_logged and elapsed >= self._long_disconnect_threshold:
                    self.long_disconnect_count += 1
                    long_disconnect_logged = True
                    self._logger.warning(
                        "binance_ws_long_disconnect",
                        elapsed_seconds=elapsed,
                        threshold_seconds=self._long_disconnect_threshold,
                    )
                self._logger.info(
                    "binance_ws_disconnect",
                    error=str(exc),
                    attempt=attempt,
                    elapsed_seconds=elapsed,
                )
                attempt += 1
                # `_async_sleep` (= asyncio.sleep) is named separately so
                # tests can patch backoff timing without touching the global
                # asyncio.sleep used elsewhere in the test code's own yields.
                await _async_sleep(next(delays))

    async def _receive_until_closed(self, ws: ClientConnection) -> None:
        """Iterate the WS until it closes; dispatch each frame to the handler.

        JSON-decode failures raise :class:`BinanceWsError` rather than
        silently dropping the frame — corrupt frames are a real outage
        signal, not noise.
        """
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise BinanceWsError(f"non-JSON frame from Binance WS: {raw!r}") from exc
            await self._handler(msg)

    async def _send_subscription(self, *, method: str, params: list[str]) -> None:
        if self._ws is None:
            raise NotConnectedError(f"_send_subscription({method}) with no open WS")
        frame = {"method": method, "params": params, "id": self._next_id}
        self._next_id += 1
        await self._ws.send(json.dumps(frame))
