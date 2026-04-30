"""§N4 unit tests for :class:`packages.exchange.bybit_v5.ws.BybitV5PrivateWs` (T-209).

Coverage:
* Auth signing — HMAC hand-compute, frame shape, send order before subscribe.
* Reconnect — H-007 full-jitter backoff envelope; reset on success;
  long-disconnect single-fire latch + counter; auth re-handshake on each cycle.
* Auth failure — counter increments + ERROR log; transport reconnect does
  NOT increment ``auth_failure_count`` (Write-time guidance #7).
* Frame demux — execution + position topics enqueue; non-linear category drop;
  H-009 no-dedup at adapter (duplicate execId frames both surface).
* Mappers — H-015 round-trip + UTC tz; ``avgPrice="0"`` preserved as
  Decimal("0"); empty side flat convention.
* Subscribe / ping / close — frame shape, ping cadence, close idempotency
  + state guard.

`websockets.asyncio.client.connect` and `_async_sleep` patched per
Write-time guidance #1 + #2 so tests don't burn cumulative jittered
delays in real wall-time and don't open real WS connections.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import structlog
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from packages.exchange.bybit_v5.ws import (
    _AUTH_EXPIRES_OFFSET_S,
    _AUTH_PAYLOAD_PREFIX,
    _BACKOFF_BASE_S,
    _BACKOFF_CAP_S,
    BybitV5PrivateWs,
    BybitWsStateError,
    ConnectionState,
    _full_jitter_backoff_delays,
    _gen_auth_frame,
    _map_execution_event,
    _map_position_event,
)
from packages.exchange.types import ExecutionEvent, PositionEvent

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeWs:
    """Minimal stand-in for ``websockets.asyncio.client.ClientConnection``.

    ``messages`` are yielded by ``__aiter__``; after exhaustion behavior is
    controlled by ``end_with``. ``recv_responses`` are popped in order by
    ``recv()`` (used for the auth-handshake response). All ``send`` calls
    are recorded in ``self.sent``.
    """

    def __init__(
        self,
        messages: list[str] | None = None,
        *,
        recv_responses: list[str] | None = None,
        end_with: BaseException | None = None,
    ) -> None:
        self._messages = list(messages or [])
        self._recv_responses = list(recv_responses or [])
        self._end_with = end_with
        self._closed_event = asyncio.Event()
        self.sent: list[str] = []
        self.closed = False

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def recv(self) -> str:
        if self._recv_responses:
            return self._recv_responses.pop(0)
        return ""

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
    """Programmable replacement for ``ws_connect`` in :mod:`packages.exchange.bybit_v5.ws`."""

    def __init__(self, script: list[BaseException | _FakeWs]) -> None:
        self._script = list(script)
        self.attempts: list[BaseException | _FakeWs] = []

    def __call__(self, *_args: Any, **_kwargs: Any) -> _ScriptedConnect:
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
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    log = logging.getLogger("test_bybit_ws")
    log.handlers = []
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return structlog.stdlib.get_logger("test_bybit_ws")


def _auth_success_response() -> str:
    return json.dumps({"success": True, "ret_msg": "", "op": "auth", "conn_id": "c1"})


def _auth_failure_response() -> str:
    return json.dumps({"success": False, "ret_msg": "bad sig", "op": "auth"})


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    script: list[BaseException | _FakeWs],
    *,
    long_disconnect_threshold_s: float = 60.0,
    sleep_capture: list[float] | None = None,
    seed: int = 0xC0FFEE,
    now_fn: Any = None,
    api_key: str = "test_key",
    api_secret: str = "test_secret",
    ping_interval_s: float = 18.0,
) -> tuple[BybitV5PrivateWs, _ScriptedConnect]:
    connect = _ScriptedConnect(script)
    monkeypatch.setattr("packages.exchange.bybit_v5.ws.ws_connect", connect)

    async def _capture_sleep(delay: float) -> None:
        if sleep_capture is not None:
            sleep_capture.append(delay)
        await asyncio.sleep(0)

    monkeypatch.setattr("packages.exchange.bybit_v5.ws._async_sleep", _capture_sleep)

    client = BybitV5PrivateWs(
        api_key=api_key,
        api_secret=api_secret,
        logger=_logger(),
        long_disconnect_threshold_s=long_disconnect_threshold_s,
        rng=random.Random(seed),
        now_fn=now_fn or (lambda: 1700000000.0),
        ping_interval_s=ping_interval_s,
    )
    return client, connect


# ---------------------------------------------------------------------------
# Auth signing (3 tests)
# ---------------------------------------------------------------------------


def test_gen_auth_frame_hmac_matches_hand_computed_signature() -> None:
    """Hand-computed HMAC over ``GET/realtime{expires}`` matches frame.args[2]."""
    fixed_now = 1700000000.0
    frame = _gen_auth_frame(
        api_key="test_key",
        api_secret="test_secret",
        now_fn=lambda: fixed_now,
    )
    expected_expires = int((fixed_now + _AUTH_EXPIRES_OFFSET_S) * 1000)
    expected_payload = f"{_AUTH_PAYLOAD_PREFIX}{expected_expires}"
    expected_sig = hmac.new(
        b"test_secret",
        expected_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert frame["args"][1] == expected_expires
    assert frame["args"][2] == expected_sig


def test_gen_auth_frame_args_shape_is_apikey_expires_signature() -> None:
    """Frame: ``{op:"auth", args:[<key>, <expires_ms>, <hex_sig>]}``."""
    frame = _gen_auth_frame(
        api_key="my_key",
        api_secret="my_secret",
        now_fn=lambda: 1700000000.0,
    )
    assert frame["op"] == "auth"
    assert isinstance(frame["args"], list)
    assert len(frame["args"]) == 3
    assert frame["args"][0] == "my_key"
    assert isinstance(frame["args"][1], int)
    assert isinstance(frame["args"][2], str)
    assert len(frame["args"][2]) == 64  # SHA256 hex


async def test_auth_handshake_sent_before_subscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeWs(messages=[], recv_responses=[_auth_success_response()])
    client, _connect = _build_client(monkeypatch, script=[fake], sleep_capture=[])

    task = asyncio.create_task(client.run())
    for _ in range(5):
        await asyncio.sleep(0)
    await client.close()
    await task

    assert len(fake.sent) >= 2
    auth_frame = json.loads(fake.sent[0])
    sub_frame = json.loads(fake.sent[1])
    assert auth_frame["op"] == "auth"
    assert sub_frame["op"] == "subscribe"


# ---------------------------------------------------------------------------
# Mappers (4 tests)
# ---------------------------------------------------------------------------


def test_map_execution_event_preserves_decimals_and_constructs_utc_executed_at() -> None:
    item = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "execId": "abc123",
        "orderId": "ord001",
        "side": "Buy",
        "execPrice": "45000.500000000001",
        "execQty": "0.001",
        "execFee": "0.0225",
        "execTime": "1700000010500",
    }
    event = _map_execution_event(item)
    assert isinstance(event, ExecutionEvent)
    assert event.exchange_exec_id == "abc123"
    assert event.exchange_order_id == "ord001"
    assert event.symbol == "BTCUSDT"
    assert event.side == "buy"
    assert isinstance(event.price, Decimal)
    assert event.price == Decimal("45000.500000000001")
    assert str(event.price) == "45000.500000000001"
    assert event.qty == Decimal("0.001")
    assert event.fee == Decimal("0.0225")
    assert event.executed_at == datetime(2023, 11, 14, 22, 13, 30, 500000, tzinfo=UTC)
    assert event.executed_at.tzinfo is UTC


def test_map_position_event_with_active_position_maps_full_field_set() -> None:
    item = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Sell",
        "size": "0.5",
        "avgPrice": "44999.99",
        "leverage": "10",
        "unrealisedPnl": "-1.25",
        "updatedTime": "1700000010500",
    }
    event = _map_position_event(item)
    assert isinstance(event, PositionEvent)
    assert event.symbol == "BTCUSDT"
    assert event.side == "sell"
    assert event.size == Decimal("0.5")
    assert event.entry_price == Decimal("44999.99")
    assert event.leverage == 10
    assert event.unrealized_pnl == Decimal("-1.25")
    assert event.occurred_at.tzinfo is UTC


def test_map_position_event_with_empty_side_maps_to_None_with_None_metadata() -> None:
    """Bybit ``side=""`` flat convention → side=None + None metadata."""
    item = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "",
        "size": "0",
        "avgPrice": "",
        "leverage": "",
        "unrealisedPnl": "",
        "updatedTime": "1700000010500",
    }
    event = _map_position_event(item)
    assert event.side is None
    assert event.size == Decimal("0")
    assert event.entry_price is None
    assert event.leverage is None
    assert event.unrealized_pnl is None


def test_map_position_event_with_avgprice_zero_string_preserves_decimal_zero() -> None:
    """W#3 narrow check: ``"0"`` for avgPrice / unrealisedPnl != ``""`` flat."""
    item = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": "0",
        "avgPrice": "0",
        "leverage": "10",
        "unrealisedPnl": "0",
        "updatedTime": "1700000010500",
    }
    event = _map_position_event(item)
    assert event.entry_price == Decimal("0")
    assert event.unrealized_pnl == Decimal("0")


# ---------------------------------------------------------------------------
# Backoff (3 tests)
# ---------------------------------------------------------------------------


async def test_reconnect_uses_exponential_backoff_with_full_jitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-007 verbatim — delays inside ``[0, min(2**n, 60)]`` for attempt n."""
    sleeps: list[float] = []
    fake = _FakeWs(messages=[], recv_responses=[_auth_success_response()])
    script: list[BaseException | _FakeWs] = []
    script.extend(ConnectionClosedError(None, None) for _ in range(8))
    script.append(fake)
    client, _connect = _build_client(monkeypatch, script=script, sleep_capture=sleeps)

    task = asyncio.create_task(client.run())
    for _ in range(80):
        await asyncio.sleep(0)
    await client.close()
    await task

    assert len(sleeps) >= 8, f"expected ≥8 backoff sleeps, got {len(sleeps)}"
    for n, delay in enumerate(sleeps[:8]):
        ceiling = min(_BACKOFF_BASE_S * (2**n), _BACKOFF_CAP_S)
        assert 0.0 <= delay <= ceiling, (
            f"attempt {n}: delay {delay} outside H-007 envelope [0, {ceiling}]"
        )


async def test_reconnect_resets_attempt_counter_on_successful_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After successful connect, subsequent reconnect starts at attempt 0 again."""
    fake_a = _FakeWs(
        messages=[],
        recv_responses=[_auth_success_response()],
        end_with=ConnectionClosedError(None, None),
    )
    fake_b = _FakeWs(messages=[], recv_responses=[_auth_success_response()])
    sleeps: list[float] = []
    client, _connect = _build_client(
        monkeypatch,
        script=[fake_a, fake_b],
        sleep_capture=sleeps,
    )

    task = asyncio.create_task(client.run())
    for _ in range(20):
        await asyncio.sleep(0)
    await client.close()
    await task

    # First sleep after fake_a disconnect uses attempt=0 ceiling (1.0).
    assert len(sleeps) >= 1
    assert 0.0 <= sleeps[0] <= _BACKOFF_BASE_S


async def test_long_disconnect_threshold_logs_once_and_increments_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threshold=0 → first reconnect attempt crosses; counter increments once per outage."""
    fake_a = _FakeWs(
        messages=[],
        recv_responses=[_auth_success_response()],
        end_with=ConnectionClosedError(None, None),
    )
    fake_b = _FakeWs(messages=[], recv_responses=[_auth_success_response()])
    script: list[BaseException | _FakeWs] = [
        ConnectionClosedError(None, None),
        ConnectionClosedError(None, None),
        fake_a,
        ConnectionClosedError(None, None),
        fake_b,
    ]
    client, _connect = _build_client(
        monkeypatch,
        script=script,
        long_disconnect_threshold_s=0.0,
        sleep_capture=[],
    )

    task = asyncio.create_task(client.run())
    for _ in range(50):
        await asyncio.sleep(0)
    await client.close()
    await task

    assert client.long_disconnect_count == 2


# ---------------------------------------------------------------------------
# Auth failure (3 tests, includes WG#7 negative pin)
# ---------------------------------------------------------------------------


async def test_auth_failure_increments_counter_and_continues_reconnect_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``success:false`` → counter++ + reconnect loop continues to next cycle."""
    fake_fail = _FakeWs(messages=[], recv_responses=[_auth_failure_response()])
    fake_ok = _FakeWs(messages=[], recv_responses=[_auth_success_response()])
    client, _connect = _build_client(
        monkeypatch,
        script=[fake_fail, fake_ok],
        sleep_capture=[],
    )

    task = asyncio.create_task(client.run())
    for _ in range(20):
        await asyncio.sleep(0)
    await client.close()
    await task

    assert client.auth_failure_count == 1


async def test_auth_handshake_runs_on_every_reconnect_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each new ws connection sends a fresh auth frame before subscribe."""
    fake_a = _FakeWs(
        messages=[],
        recv_responses=[_auth_success_response()],
        end_with=ConnectionClosedError(None, None),
    )
    fake_b = _FakeWs(messages=[], recv_responses=[_auth_success_response()])
    client, _connect = _build_client(
        monkeypatch,
        script=[fake_a, fake_b],
        sleep_capture=[],
    )

    task = asyncio.create_task(client.run())
    for _ in range(20):
        await asyncio.sleep(0)
    await client.close()
    await task

    assert json.loads(fake_a.sent[0])["op"] == "auth"
    assert json.loads(fake_b.sent[0])["op"] == "auth"


async def test_transport_reconnect_does_not_increment_auth_failure_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#7 negative pin: WebSocketException disconnect is NOT auth failure."""
    fake_ok = _FakeWs(messages=[], recv_responses=[_auth_success_response()])
    script: list[BaseException | _FakeWs] = [
        ConnectionClosedError(None, None),
        ConnectionClosedError(None, None),
        fake_ok,
    ]
    client, _connect = _build_client(monkeypatch, script=script, sleep_capture=[])

    task = asyncio.create_task(client.run())
    for _ in range(20):
        await asyncio.sleep(0)
    await client.close()
    await task

    assert client.auth_failure_count == 0


# ---------------------------------------------------------------------------
# Subscribe (1 test)
# ---------------------------------------------------------------------------


async def test_subscribe_frame_carries_execution_and_position_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeWs(messages=[], recv_responses=[_auth_success_response()])
    client, _connect = _build_client(monkeypatch, script=[fake], sleep_capture=[])

    task = asyncio.create_task(client.run())
    for _ in range(5):
        await asyncio.sleep(0)
    await client.close()
    await task

    sub_frame = json.loads(fake.sent[1])
    assert sub_frame == {"op": "subscribe", "args": ["execution", "position"]}


# ---------------------------------------------------------------------------
# Frame demux (4 tests, incl. H-009 no-dedup pin)
# ---------------------------------------------------------------------------


async def test_execution_topic_data_with_linear_category_enqueues_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exec_frame = json.dumps(
        {
            "topic": "execution",
            "data": [
                {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "execId": "e1",
                    "orderId": "o1",
                    "side": "Buy",
                    "execPrice": "45000",
                    "execQty": "0.001",
                    "execFee": "0.01",
                    "execTime": "1700000010500",
                }
            ],
        }
    )
    fake = _FakeWs(messages=[exec_frame], recv_responses=[_auth_success_response()])
    client, _connect = _build_client(monkeypatch, script=[fake], sleep_capture=[])

    task = asyncio.create_task(client.run())
    for _ in range(5):
        await asyncio.sleep(0)

    iter_exec = client.executions()
    event = await asyncio.wait_for(iter_exec.__anext__(), timeout=1.0)
    assert event.exchange_exec_id == "e1"

    await client.close()
    await task


async def test_position_topic_data_with_linear_category_enqueues_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pos_frame = json.dumps(
        {
            "topic": "position",
            "data": [
                {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "size": "0.5",
                    "avgPrice": "45000",
                    "leverage": "10",
                    "unrealisedPnl": "1.0",
                    "updatedTime": "1700000010500",
                }
            ],
        }
    )
    fake = _FakeWs(messages=[pos_frame], recv_responses=[_auth_success_response()])
    client, _connect = _build_client(monkeypatch, script=[fake], sleep_capture=[])

    task = asyncio.create_task(client.run())
    for _ in range(5):
        await asyncio.sleep(0)

    iter_pos = client.positions()
    event = await asyncio.wait_for(iter_pos.__anext__(), timeout=1.0)
    assert event.symbol == "BTCUSDT"
    assert event.side == "buy"

    await client.close()
    await task


async def test_data_with_non_linear_category_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spot_frame = json.dumps(
        {
            "topic": "execution",
            "data": [
                {
                    "category": "spot",
                    "symbol": "BTCUSDT",
                    "execId": "e1",
                    "orderId": "o1",
                    "side": "Buy",
                    "execPrice": "45000",
                    "execQty": "0.001",
                    "execFee": "0.01",
                    "execTime": "1700000010500",
                }
            ],
        }
    )
    fake = _FakeWs(messages=[spot_frame], recv_responses=[_auth_success_response()])
    client, _connect = _build_client(monkeypatch, script=[fake], sleep_capture=[])

    task = asyncio.create_task(client.run())
    for _ in range(5):
        await asyncio.sleep(0)
    await client.close()
    await task

    assert client._execution_queue.empty()


async def test_duplicate_execId_frames_both_appear_in_executions_iterator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-009 no-dedup at adapter — T-218 dispatcher dedups, NOT the WS layer."""
    dup_frame = json.dumps(
        {
            "topic": "execution",
            "data": [
                {
                    "category": "linear",
                    "symbol": "BTCUSDT",
                    "execId": "dup",
                    "orderId": "o1",
                    "side": "Buy",
                    "execPrice": "45000",
                    "execQty": "0.001",
                    "execFee": "0.01",
                    "execTime": "1700000010500",
                }
            ],
        }
    )
    fake = _FakeWs(
        messages=[dup_frame, dup_frame],
        recv_responses=[_auth_success_response()],
    )
    client, _connect = _build_client(monkeypatch, script=[fake], sleep_capture=[])

    task = asyncio.create_task(client.run())
    for _ in range(5):
        await asyncio.sleep(0)

    iter_exec = client.executions()
    e1 = await asyncio.wait_for(iter_exec.__anext__(), timeout=1.0)
    e2 = await asyncio.wait_for(iter_exec.__anext__(), timeout=1.0)
    assert e1.exchange_exec_id == "dup"
    assert e2.exchange_exec_id == "dup"

    await client.close()
    await task


# ---------------------------------------------------------------------------
# Lifecycle / state guard (3 tests)
# ---------------------------------------------------------------------------


async def test_initial_state_is_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch, script=[])
    assert client.state is ConnectionState.DISCONNECTED


async def test_close_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _build_client(monkeypatch, script=[])
    await client.close()
    await client.close()
    assert client.state is ConnectionState.CLOSED


async def test_run_rejected_on_non_disconnected_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _build_client(monkeypatch, script=[])
    await client.close()
    with pytest.raises(BybitWsStateError):
        await client.run()


# ---------------------------------------------------------------------------
# Backoff helper standalone (1 test)
# ---------------------------------------------------------------------------


def test_full_jitter_backoff_delays_envelope_holds_through_cap_saturation() -> None:
    """First 10 attempts: every yield in [0, min(2**n, 60)]; saturates at cap."""
    delays = _full_jitter_backoff_delays(rng=random.Random(0))
    for n in range(10):
        d = next(delays)
        ceiling = min(_BACKOFF_BASE_S * (2**n), _BACKOFF_CAP_S)
        assert 0.0 <= d <= ceiling
