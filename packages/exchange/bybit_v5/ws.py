"""§11.2 BybitV5PrivateWs — long-lived authenticated WS for Bybit V5 (T-209).

Owns the receive loop + auth handshake + reconnect with full-jitter
exponential backoff (H-007). Demuxes inbound frames by ``topic`` to
per-stream :class:`asyncio.Queue` instances. ``executions()`` /
``positions()`` are :class:`AsyncIterator` factories draining those
queues; ``def`` (NOT ``async def``) per T-201 OQ-1, mirroring
:class:`packages.exchange.paper.PaperExchange`.

Not on the :class:`packages.exchange.protocols.ExchangeClient` Protocol
— :class:`BybitV5Adapter` delegates to this class (T-215 wires the
``ws`` ctor kwarg).

H-009 dedup contract: adapter does NOT dedup; T-218 dispatcher dedups
by ``execId`` via :class:`packages.bus.DedupingConsumer` (ring 10k).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import random
import time
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from packages.exchange.types import ExecutionEvent, PositionEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from structlog.stdlib import BoundLogger
    from websockets.asyncio.client import ClientConnection


__all__ = ["BybitV5PrivateWs", "BybitWsStateError", "ConnectionState"]


# Protocol-binding constants (Bybit V5 spec literals; NOT ctor-tunable per L-001).
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 60.0
_AUTH_PAYLOAD_PREFIX = "GET/realtime"
_AUTH_EXPIRES_OFFSET_S = 5

# Operator-tunable defaults (ctor kwarg overrides per §N9).
_DEFAULT_WS_URL = "wss://stream.bybit.com/v5/private"
_DEFAULT_PING_INTERVAL_S = 18.0
_DEFAULT_LONG_DISCONNECT_THRESHOLD_S = 60.0
_DEFAULT_NATIVE_PING_INTERVAL_S = 20.0
_DEFAULT_NATIVE_PING_TIMEOUT_S = 10.0


# Patchable seam for backoff sleep (Write-time guidance #1) — tests patch
# `packages.exchange.bybit_v5.ws._async_sleep` so reconnect-with-backoff
# tests don't pay cumulative jittered delay in real wall-time.
_async_sleep = asyncio.sleep

logger = logging.getLogger(__name__)


class BybitWsStateError(RuntimeError):
    """``run()`` re-entry guard — raised when called from non-DISCONNECTED."""


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSED = "closed"


def _full_jitter_backoff_delays(
    rng: random.Random | None = None,
) -> Iterator[float]:
    """H-007 full-jitter backoff: ``uniform(0, min(2**n, 60))`` forever."""
    rand = rng if rng is not None else random
    attempt = 0
    while True:
        ceiling = min(_BACKOFF_BASE_S * (2**attempt), _BACKOFF_CAP_S)
        yield rand.uniform(0.0, ceiling)  # nosec B311 — H-007 reconnect jitter, not cryptographic
        attempt += 1


def _gen_auth_frame(
    *,
    api_key: str,
    api_secret: str,
    now_fn: Callable[[], float],
) -> dict[str, Any]:
    """Bybit V5 private-WS auth frame; HMAC-SHA256 over ``GET/realtime{expires}``."""
    expires = int((now_fn() + _AUTH_EXPIRES_OFFSET_S) * 1000)
    payload = f"{_AUTH_PAYLOAD_PREFIX}{expires}"
    signature = hmac.new(
        api_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"op": "auth", "args": [api_key, expires, signature]}


def _map_execution_event(item: dict[str, Any]) -> ExecutionEvent:
    """Bybit ``execution`` row → ExecutionEvent. H-015 round-trip via Decimal(str(...))."""
    side_raw = item["side"]
    side: Literal["buy", "sell"] = "buy" if side_raw == "Buy" else "sell"
    return ExecutionEvent(
        exchange_exec_id=str(item["execId"]),
        exchange_order_id=str(item["orderId"]),
        symbol=str(item["symbol"]),
        side=side,
        price=Decimal(str(item["execPrice"])),
        qty=Decimal(str(item["execQty"])),
        fee=Decimal(str(item["execFee"])),
        executed_at=datetime.fromtimestamp(int(item["execTime"]) / 1000, tz=UTC),
    )


def _map_position_event(item: dict[str, Any]) -> PositionEvent:
    """Bybit ``position`` row → PositionEvent. Mirrors T-208b ``_map_position_row``."""
    side_raw = item["side"]
    if side_raw == "Buy":
        side: Literal["buy", "sell"] | None = "buy"
    elif side_raw == "Sell":
        side = "sell"
    else:
        side = None
    avg_price = item.get("avgPrice")
    entry_price = Decimal(str(avg_price)) if avg_price not in ("", None) else None
    leverage_raw = item.get("leverage")
    leverage = int(str(leverage_raw)) if leverage_raw not in ("", None) else None
    unrealized_raw = item.get("unrealisedPnl")
    unrealized_pnl = Decimal(str(unrealized_raw)) if unrealized_raw not in ("", None) else None
    return PositionEvent(
        symbol=str(item["symbol"]),
        side=side,
        size=Decimal(str(item["size"])),
        entry_price=entry_price,
        leverage=leverage,
        unrealized_pnl=unrealized_pnl,
        occurred_at=datetime.fromtimestamp(int(item["updatedTime"]) / 1000, tz=UTC),
    )


class BybitV5PrivateWs:
    """Long-lived authenticated WS for Bybit V5 ``execution`` + ``position`` topics."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        logger: BoundLogger,
        ws_url: str = _DEFAULT_WS_URL,
        ping_interval_s: float = _DEFAULT_PING_INTERVAL_S,
        long_disconnect_threshold_s: float = _DEFAULT_LONG_DISCONNECT_THRESHOLD_S,
        rng: random.Random | None = None,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._logger = logger
        self._ws_url = ws_url
        self._ping_interval_s = ping_interval_s
        self._long_disconnect_threshold_s = long_disconnect_threshold_s
        self._rng = rng
        self._now_fn = now_fn
        self._state = ConnectionState.DISCONNECTED
        self._ws: ClientConnection | None = None
        self._execution_queue: asyncio.Queue[ExecutionEvent] = asyncio.Queue()
        self._position_queue: asyncio.Queue[PositionEvent] = asyncio.Queue()
        self.long_disconnect_count: int = 0
        self.auth_failure_count: int = 0

    @property
    def state(self) -> ConnectionState:
        return self._state

    async def run(self) -> None:
        if self._state is not ConnectionState.DISCONNECTED:
            msg = f"run() called in state {self._state.value!r}; expected 'disconnected'"
            raise BybitWsStateError(msg)
        await self._reconnect_loop()

    async def close(self) -> None:
        if self._state is ConnectionState.CLOSED:
            return
        self._state = ConnectionState.CLOSED
        if self._ws is not None:
            with contextlib.suppress(WebSocketException):
                await self._ws.close()
        self._ws = None

    def executions(self) -> AsyncIterator[ExecutionEvent]:
        return self._iter_executions()

    async def _iter_executions(self) -> AsyncIterator[ExecutionEvent]:
        while True:
            yield await self._execution_queue.get()

    def positions(self) -> AsyncIterator[PositionEvent]:
        return self._iter_positions()

    async def _iter_positions(self) -> AsyncIterator[PositionEvent]:
        while True:
            yield await self._position_queue.get()

    async def _reconnect_loop(self) -> None:
        delays = _full_jitter_backoff_delays(rng=self._rng)
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
                async with ws_connect(
                    self._ws_url,
                    ping_interval=_DEFAULT_NATIVE_PING_INTERVAL_S,
                    ping_timeout=_DEFAULT_NATIVE_PING_TIMEOUT_S,
                ) as ws:
                    self._ws = ws
                    self._state = ConnectionState.CONNECTED
                    self._logger.info(
                        "bybit_private_ws_connected",
                        url=self._ws_url,
                        attempt=attempt,
                    )
                    auth_ok = await self._authenticate(ws)
                    if not auth_ok:
                        with contextlib.suppress(WebSocketException):
                            await ws.close()
                        self._ws = None
                        if disconnect_started_at is None:
                            disconnect_started_at = time.monotonic()
                        attempt += 1
                        await _async_sleep(next(delays))
                        continue
                    await self._subscribe(ws)
                    disconnect_started_at = None
                    long_disconnect_logged = False
                    attempt = 0
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
                        await self._receive_until_closed(ws)
                    finally:
                        ping_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ping_task
            except (WebSocketException, OSError) as exc:
                if self._state is ConnectionState.CLOSED:
                    return
                self._ws = None
                if disconnect_started_at is None:
                    disconnect_started_at = time.monotonic()
                elapsed = time.monotonic() - disconnect_started_at
                if not long_disconnect_logged and elapsed >= self._long_disconnect_threshold_s:
                    self.long_disconnect_count += 1
                    long_disconnect_logged = True
                    self._logger.warning(
                        "bybit_private_ws_long_disconnect",
                        elapsed_seconds=elapsed,
                        threshold_seconds=self._long_disconnect_threshold_s,
                    )
                self._logger.info(
                    "bybit_private_ws_disconnect",
                    error=str(exc),
                    attempt=attempt,
                    elapsed_seconds=elapsed,
                )
                attempt += 1
                await _async_sleep(next(delays))

    async def _authenticate(self, ws: ClientConnection) -> bool:
        frame = _gen_auth_frame(
            api_key=self._api_key,
            api_secret=self._api_secret,
            now_fn=self._now_fn,
        )
        await ws.send(json.dumps(frame))
        response = await ws.recv()
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            data = {}
        if data.get("success") is True:
            return True
        self.auth_failure_count += 1
        self._logger.error(
            "bybit_private_ws_auth_failed",
            response=str(response)[:200],
        )
        return False

    async def _subscribe(self, ws: ClientConnection) -> None:
        frame = {"op": "subscribe", "args": ["execution", "position"]}
        await ws.send(json.dumps(frame))

    async def _ping_loop(self, ws: ClientConnection) -> None:
        try:
            while True:
                await asyncio.sleep(self._ping_interval_s)
                await ws.send(json.dumps({"op": "ping"}))
        except (asyncio.CancelledError, WebSocketException):
            pass

    async def _receive_until_closed(self, ws: ClientConnection) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self._logger.warning("bybit_private_ws_non_json_frame", raw=str(raw)[:200])
                continue
            self._on_frame(msg)

    def _on_frame(self, msg: dict[str, Any]) -> None:
        topic = msg.get("topic", "")
        data_items = msg.get("data", [])
        if topic == "execution":
            for item in data_items:
                if item.get("category") != "linear":
                    continue
                self._execution_queue.put_nowait(_map_execution_event(item))
        elif topic == "position":
            for item in data_items:
                if item.get("category") != "linear":
                    continue
                self._position_queue.put_nowait(_map_position_event(item))
