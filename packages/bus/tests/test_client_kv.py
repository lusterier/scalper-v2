"""Unit tests for :meth:`NatsClient.kv_put` / ``kv_get`` / ``kv_update``.

Mirrors :mod:`packages.bus.tests.test_client` mock surface: ``nats.connect``
patched via ``monkeypatch`` to return an ``AsyncMock`` ``nc`` whose
``jetstream()`` returns a ``MagicMock`` ``js``. The KV facet is added
on top: ``js.key_value(bucket)`` returns a ``MagicMock`` whose ``put`` /
``get`` / ``update`` are ``AsyncMock``.

T-205 extension: ``kv_get`` (read with revision; @idempotent) +
``kv_update`` (CAS write with revision check; @non_idempotent) for the
shared rate limiter (ADR-0003).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import nats.errors
import nats.js.errors
import pytest

from packages.bus import ConnectionState, NatsClient, NotConnectedError, PublishError
from packages.core import is_idempotent, is_non_idempotent


@pytest.fixture
def logger() -> MagicMock:
    stub = MagicMock()
    for method in ("info", "warning", "error", "debug"):
        setattr(stub, method, MagicMock())
    return stub


@pytest.fixture
def nats_kv_mocks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch ``nats.connect`` and assemble the KV mock surface."""
    nc = AsyncMock()
    js = AsyncMock()
    kv = MagicMock()
    kv.put = AsyncMock(return_value=42)
    kv.get = AsyncMock()
    kv.update = AsyncMock(return_value=43)

    nc.jetstream = MagicMock(return_value=js)
    nc.close = AsyncMock()
    js.key_value = AsyncMock(return_value=kv)

    connect = AsyncMock(return_value=nc)
    monkeypatch.setattr("packages.bus.client.nats.connect", connect)
    return SimpleNamespace(nc=nc, js=js, kv=kv, connect=connect)


@pytest.fixture
def client(logger: MagicMock) -> NatsClient:
    return NatsClient(servers=["nats://localhost:4222"], name="unit-test", logger=logger)


@pytest.mark.asyncio
async def test_kv_put_calls_js_key_value_then_put(
    client: NatsClient, nats_kv_mocks: SimpleNamespace
) -> None:
    await client.connect()
    await client.kv_put("feature_latest", "ind.x:BTCUSDT", b"payload")
    nats_kv_mocks.js.key_value.assert_awaited_once_with("feature_latest")
    nats_kv_mocks.kv.put.assert_awaited_once_with("ind.x:BTCUSDT", b"payload")


@pytest.mark.asyncio
async def test_kv_put_returns_revision(client: NatsClient, nats_kv_mocks: SimpleNamespace) -> None:
    await client.connect()
    revision = await client.kv_put("feature_latest", "k", b"v")
    assert revision == 42


@pytest.mark.asyncio
async def test_kv_put_from_disconnected_raises_NotConnectedError(
    client: NatsClient,
) -> None:
    """No connect — kv_put rejects with state guard."""
    assert client.state is ConnectionState.DISCONNECTED
    with pytest.raises(NotConnectedError, match="kv_put called in state 'disconnected'"):
        await client.kv_put("feature_latest", "k", b"v")


@pytest.mark.asyncio
async def test_kv_put_js_error_wrapped_as_PublishError(
    client: NatsClient, nats_kv_mocks: SimpleNamespace
) -> None:
    """Bucket-not-found (or any nats.errors.Error) becomes PublishError."""
    await client.connect()
    nats_kv_mocks.js.key_value.side_effect = nats.errors.Error("bucket not found")
    with pytest.raises(PublishError, match="kv_put to 'missing'"):
        await client.kv_put("missing", "k", b"v")


@pytest.mark.asyncio
async def test_kv_put_value_bytes_passed_through(
    client: NatsClient, nats_kv_mocks: SimpleNamespace
) -> None:
    """Bytes value passes through unchanged; no encoding magic."""
    await client.connect()
    payload = b"\x00\x01\x02\xff arbitrary binary"
    await client.kv_put("feature_latest", "k", payload)
    call_args = nats_kv_mocks.kv.put.await_args
    assert call_args is not None
    assert call_args.args == ("k", payload)


def test_kv_put_decorator_marker_is_idempotent() -> None:
    """`@idempotent` decorator registers via the public `is_idempotent` helper.

    Mirror of ``test_publish_is_marked_non_idempotent`` precedent in
    ``test_client.py``: assertion via the public marker registry, not
    the private ``__idempotent__`` dunder.
    """
    assert is_idempotent(NatsClient.kv_put)


# --- T-205: kv_get + kv_update extensions ---------------------------------


@pytest.mark.asyncio
async def test_kv_get_returns_value_and_revision_for_existing_key(
    client: NatsClient, nats_kv_mocks: SimpleNamespace
) -> None:
    """Happy path: kv_get returns (value, revision) tuple from KV entry."""
    entry = MagicMock()
    entry.value = b"payload"
    entry.revision = 7
    nats_kv_mocks.kv.get = AsyncMock(return_value=entry)
    await client.connect()
    result = await client.kv_get("rate_limits", "bybit:sub-a:orders")
    assert result == (b"payload", 7)
    nats_kv_mocks.js.key_value.assert_awaited_with("rate_limits")
    nats_kv_mocks.kv.get.assert_awaited_once_with("bybit:sub-a:orders")


@pytest.mark.asyncio
async def test_kv_get_returns_none_for_missing_key(
    client: NatsClient, nats_kv_mocks: SimpleNamespace
) -> None:
    """KeyNotFoundError → None (caller treats as fresh state)."""
    nats_kv_mocks.kv.get = AsyncMock(side_effect=nats.js.errors.KeyNotFoundError())
    await client.connect()
    result = await client.kv_get("rate_limits", "missing-key")
    assert result is None


@pytest.mark.asyncio
async def test_kv_get_raises_publish_error_on_other_nats_error(
    client: NatsClient, nats_kv_mocks: SimpleNamespace
) -> None:
    """Non-KeyNotFound nats.errors.Error wrapped as PublishError."""
    await client.connect()
    nats_kv_mocks.js.key_value.side_effect = nats.errors.Error("bucket missing")
    with pytest.raises(PublishError, match="kv_get from 'rate_limits'"):
        await client.kv_get("rate_limits", "key")


@pytest.mark.asyncio
async def test_kv_get_from_disconnected_raises_NotConnectedError(
    client: NatsClient,
) -> None:
    """No connect → kv_get rejects via state guard (parity with kv_put)."""
    assert client.state is ConnectionState.DISCONNECTED
    with pytest.raises(NotConnectedError, match="kv_get called in state 'disconnected'"):
        await client.kv_get("rate_limits", "k")


@pytest.mark.asyncio
async def test_kv_update_calls_js_with_last_revision(
    client: NatsClient, nats_kv_mocks: SimpleNamespace
) -> None:
    """CAS update passes last_revision to NATS via ``last=`` kwarg."""
    await client.connect()
    new_revision = await client.kv_update("rate_limits", "k", b"v", 7)
    assert new_revision == 43
    nats_kv_mocks.kv.update.assert_awaited_once_with("k", b"v", last=7)


@pytest.mark.asyncio
async def test_kv_update_raises_publish_error_on_revision_mismatch(
    client: NatsClient, nats_kv_mocks: SimpleNamespace
) -> None:
    """KeyWrongLastSequenceError (CAS conflict) → PublishError."""
    await client.connect()
    nats_kv_mocks.kv.update = AsyncMock(side_effect=nats.errors.Error("wrong last sequence"))
    with pytest.raises(PublishError, match="kv_update to 'rate_limits'"):
        await client.kv_update("rate_limits", "k", b"v", 7)


@pytest.mark.asyncio
async def test_kv_update_from_disconnected_raises_NotConnectedError(
    client: NatsClient,
) -> None:
    """No connect → kv_update rejects via state guard."""
    assert client.state is ConnectionState.DISCONNECTED
    with pytest.raises(NotConnectedError, match="kv_update called in state 'disconnected'"):
        await client.kv_update("rate_limits", "k", b"v", 1)


def test_kv_get_decorator_marker_is_idempotent() -> None:
    """`@idempotent` per Decision #6 — read-only, replays yield same state."""
    assert is_idempotent(NatsClient.kv_get)


def test_kv_update_decorator_marker_is_non_idempotent() -> None:
    """`@non_idempotent` per Decision #6 — CAS replay yields different outcome."""
    assert is_non_idempotent(NatsClient.kv_update)
