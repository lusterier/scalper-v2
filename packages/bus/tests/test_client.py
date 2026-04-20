"""Unit tests for :class:`packages.bus.NatsClient` (§8, §5.7).

``nats.connect`` is patched via ``monkeypatch`` to return an
``AsyncMock`` stand-in for ``nats.aio.client.Client``. The JetStream
context (``nc.jetstream()``) is sync in ``nats-py`` but returns an
async object, so it is attached as a ``MagicMock`` whose return value
is an ``AsyncMock``. Reconnect/disconnect callbacks are exercised by
grabbing them off ``mock_connect.call_args.kwargs`` and awaiting them
directly — no real network I/O.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest

from packages.bus import (
    ConnectionState,
    MessageEnvelope,
    NatsClient,
    NotConnectedError,
    PublishError,
    SubscribeError,
)
from packages.core import CorrelationId, is_non_idempotent


@pytest.fixture
def logger() -> MagicMock:
    """Structlog-shaped logger stub with the four level methods used by the bus."""
    stub = MagicMock()
    for method in ("info", "warning", "error", "debug"):
        setattr(stub, method, MagicMock())
    return stub


@pytest.fixture
def nats_mocks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch ``nats.connect`` and return the assembled mock surface."""
    nc = AsyncMock()
    js = AsyncMock()
    sub = AsyncMock()
    ack = MagicMock()
    ack.duplicate = False

    nc.jetstream = MagicMock(return_value=js)
    nc.subscribe = AsyncMock(return_value=sub)
    nc.close = AsyncMock()
    js.publish = AsyncMock(return_value=ack)
    sub.drain = AsyncMock()

    connect = AsyncMock(return_value=nc)
    monkeypatch.setattr("packages.bus.client.nats.connect", connect)
    return SimpleNamespace(nc=nc, js=js, sub=sub, ack=ack, connect=connect)


@pytest.fixture
def client(logger: MagicMock) -> NatsClient:
    return NatsClient(servers=["nats://localhost:4222"], name="unit-test", logger=logger)


@pytest.fixture
def envelope() -> MessageEnvelope:
    return MessageEnvelope(
        correlation_id=CorrelationId("cid-1"),
        publisher="signal-gateway",
        payload={"action": "LONG"},
    )


def _event_names(mock_method: MagicMock) -> list[str]:
    """Extract structlog event names from a mock logger-method's call list."""
    return [call.args[0] for call in mock_method.call_args_list if call.args]


@pytest.mark.asyncio
async def test_initial_state_is_disconnected(client: NatsClient) -> None:
    assert client.state is ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_connect_transitions_to_connected_and_logs(
    client: NatsClient, nats_mocks: SimpleNamespace, logger: MagicMock
) -> None:
    await client.connect()
    assert client.state is ConnectionState.CONNECTED
    nats_mocks.connect.assert_awaited_once()
    kwargs = nats_mocks.connect.call_args.kwargs
    assert kwargs["servers"] == ["nats://localhost:4222"]
    assert kwargs["name"] == "unit-test"
    assert kwargs["max_reconnect_attempts"] == -1
    assert "bus_connect_started" in _event_names(logger.info)
    assert "bus_connected" in _event_names(logger.info)


@pytest.mark.asyncio
async def test_connect_from_non_disconnected_raises(
    client: NatsClient, nats_mocks: SimpleNamespace
) -> None:
    await client.connect()
    with pytest.raises(NotConnectedError):
        await client.connect()


@pytest.mark.asyncio
async def test_connect_failure_resets_state_and_logs(
    client: NatsClient,
    monkeypatch: pytest.MonkeyPatch,
    logger: MagicMock,
) -> None:
    boom = nats.errors.Error("no servers available")
    monkeypatch.setattr("packages.bus.client.nats.connect", AsyncMock(side_effect=boom))
    with pytest.raises(nats.errors.Error):
        await client.connect()
    assert client.state is ConnectionState.DISCONNECTED
    assert "bus_connect_failed" in _event_names(logger.error)


@pytest.mark.asyncio
async def test_publish_uses_js_with_nats_msg_id_header_and_logs(
    client: NatsClient,
    nats_mocks: SimpleNamespace,
    envelope: MessageEnvelope,
    logger: MagicMock,
) -> None:
    await client.connect()
    await client.publish("signals.validated", envelope)
    nats_mocks.js.publish.assert_awaited_once()
    args, kwargs = nats_mocks.js.publish.call_args
    assert args[0] == "signals.validated"
    assert args[1] == envelope.to_bytes()
    assert kwargs["headers"] == {"Nats-Msg-Id": str(envelope.message_id)}
    assert "bus_published" in _event_names(logger.debug)


@pytest.mark.asyncio
async def test_publish_duplicate_ack_logs_deduplicated_not_published(
    client: NatsClient,
    nats_mocks: SimpleNamespace,
    envelope: MessageEnvelope,
    logger: MagicMock,
) -> None:
    nats_mocks.ack.duplicate = True
    await client.connect()
    await client.publish("signals.validated", envelope)
    debug_events = _event_names(logger.debug)
    assert "bus_publish_deduplicated" in debug_events
    assert "bus_published" not in debug_events


@pytest.mark.asyncio
async def test_publish_wraps_nats_error_in_publish_error_with_cause(
    client: NatsClient,
    nats_mocks: SimpleNamespace,
    envelope: MessageEnvelope,
    logger: MagicMock,
) -> None:
    original = nats.errors.Error("stream not found")
    nats_mocks.js.publish = AsyncMock(side_effect=original)
    await client.connect()
    with pytest.raises(PublishError) as exc_info:
        await client.publish("signals.validated", envelope)
    assert exc_info.value.__cause__ is original
    assert "bus_publish_failed" in _event_names(logger.error)


@pytest.mark.asyncio
async def test_publish_before_connect_raises_not_connected(
    client: NatsClient, envelope: MessageEnvelope
) -> None:
    with pytest.raises(NotConnectedError):
        await client.publish("signals.validated", envelope)


@pytest.mark.asyncio
async def test_subscribe_tracks_subscription_and_dispatches_handler(
    client: NatsClient,
    nats_mocks: SimpleNamespace,
    envelope: MessageEnvelope,
    logger: MagicMock,
) -> None:
    await client.connect()
    received: list[MessageEnvelope] = []

    async def handler(env: MessageEnvelope) -> None:
        received.append(env)

    sub = await client.subscribe("signals.validated", handler)
    assert sub is nats_mocks.sub
    assert "bus_subscribed" in _event_names(logger.info)

    dispatcher = nats_mocks.nc.subscribe.call_args.kwargs["cb"]
    msg = MagicMock()
    msg.subject = "signals.validated"
    msg.data = envelope.to_bytes()
    await dispatcher(msg)

    assert len(received) == 1
    assert received[0].message_id == envelope.message_id
    assert "bus_message_received" in _event_names(logger.debug)


@pytest.mark.asyncio
async def test_subscribe_handler_exception_is_logged_and_swallowed(
    client: NatsClient,
    nats_mocks: SimpleNamespace,
    envelope: MessageEnvelope,
    logger: MagicMock,
) -> None:
    await client.connect()

    async def bad_handler(_env: MessageEnvelope) -> None:
        raise RuntimeError("boom")

    await client.subscribe("signals.validated", bad_handler)
    dispatcher = nats_mocks.nc.subscribe.call_args.kwargs["cb"]
    msg = MagicMock()
    msg.subject = "signals.validated"
    msg.data = envelope.to_bytes()
    await dispatcher(msg)  # must not raise

    assert "bus_handler_failed" in _event_names(logger.error)


@pytest.mark.asyncio
async def test_subscribe_envelope_parse_failure_logged_and_handler_skipped(
    client: NatsClient, nats_mocks: SimpleNamespace, logger: MagicMock
) -> None:
    await client.connect()
    called = False

    async def handler(_env: MessageEnvelope) -> None:
        nonlocal called
        called = True

    await client.subscribe("signals.validated", handler)
    dispatcher = nats_mocks.nc.subscribe.call_args.kwargs["cb"]
    msg = MagicMock()
    msg.subject = "signals.validated"
    msg.data = b"not-json"
    await dispatcher(msg)

    assert called is False
    assert "bus_handler_failed" in _event_names(logger.error)


@pytest.mark.asyncio
async def test_subscribe_wraps_nats_error_in_subscribe_error_with_cause(
    client: NatsClient, nats_mocks: SimpleNamespace
) -> None:
    original = nats.errors.Error("no responders")
    nats_mocks.nc.subscribe = AsyncMock(side_effect=original)
    await client.connect()

    async def handler(_env: MessageEnvelope) -> None:
        pass

    with pytest.raises(SubscribeError) as exc_info:
        await client.subscribe("signals.validated", handler)
    assert exc_info.value.__cause__ is original


@pytest.mark.asyncio
async def test_subscribe_before_connect_raises_not_connected(
    client: NatsClient,
) -> None:
    async def handler(_env: MessageEnvelope) -> None:
        pass

    with pytest.raises(NotConnectedError):
        await client.subscribe("signals.validated", handler)


@pytest.mark.asyncio
async def test_close_drains_subscriptions_and_transitions_to_closed(
    client: NatsClient, nats_mocks: SimpleNamespace, logger: MagicMock
) -> None:
    await client.connect()

    async def handler(_env: MessageEnvelope) -> None:
        pass

    await client.subscribe("signals.validated", handler)
    await client.close()

    nats_mocks.sub.drain.assert_awaited_once()
    nats_mocks.nc.close.assert_awaited_once()
    assert client.state is ConnectionState.CLOSED
    info_events = _event_names(logger.info)
    assert "bus_closing" in info_events
    assert "bus_closed" in info_events


@pytest.mark.asyncio
async def test_close_continues_when_one_drain_fails(
    client: NatsClient, nats_mocks: SimpleNamespace, logger: MagicMock
) -> None:
    """A drain exception on one subscription must not block nc.close()."""
    await client.connect()

    async def handler(_env: MessageEnvelope) -> None:
        pass

    await client.subscribe("signals.validated", handler)
    nats_mocks.sub.drain = AsyncMock(side_effect=RuntimeError("drain boom"))

    await client.close()  # must not raise

    nats_mocks.nc.close.assert_awaited_once()
    assert client.state is ConnectionState.CLOSED
    assert "bus_drain_failed" in _event_names(logger.warning)


@pytest.mark.asyncio
async def test_close_from_disconnected_is_noop(
    client: NatsClient, nats_mocks: SimpleNamespace
) -> None:
    await client.close()
    assert client.state is ConnectionState.DISCONNECTED
    nats_mocks.nc.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_from_connecting_raises(
    client: NatsClient,
    nats_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """close() must refuse CONNECTING — caller must let connect() resolve first."""
    blocker = asyncio.Event()

    async def hanging_connect(**_kw: Any) -> Any:
        await blocker.wait()
        return nats_mocks.nc

    monkeypatch.setattr("packages.bus.client.nats.connect", hanging_connect)

    connect_task = asyncio.create_task(client.connect())
    await asyncio.sleep(0)  # yield so connect_task advances to CONNECTING
    assert client.state is ConnectionState.CONNECTING

    with pytest.raises(NotConnectedError):
        await client.close()

    blocker.set()
    await connect_task


@pytest.mark.asyncio
async def test_disconnected_and_reconnected_callbacks_log_events(
    client: NatsClient, nats_mocks: SimpleNamespace, logger: MagicMock
) -> None:
    await client.connect()
    kwargs = nats_mocks.connect.call_args.kwargs
    await kwargs["disconnected_cb"]()
    await kwargs["reconnected_cb"]()
    assert "bus_disconnected" in _event_names(logger.warning)
    assert "bus_reconnected" in _event_names(logger.info)


def test_publish_is_marked_non_idempotent() -> None:
    """§5.8 marker must survive the :func:`non_idempotent` decorator."""
    assert is_non_idempotent(NatsClient.publish)
