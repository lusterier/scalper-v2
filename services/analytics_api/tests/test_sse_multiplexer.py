"""Unit tests for :mod:`services.analytics_api.app.sse` (T-408).

Mocks the :class:`packages.bus.NatsClient` interface (subscribe / unsubscribe).
Pin the multiplexer contracts:

* register_client creates per-subject subscriptions (shared subjects dedup).
* unregister_client + shutdown are idempotent (WG#4 + WG#5).
* connection cap raises SSEConnectionLimitError (WG#3).
* event_type filter splits POSITIONS (close + sl_moved) vs TRADES
  (placed + filled) — disjoint sets per CONCERN #3 fix.
* Drop-oldest queue overflow + rate-limited overflow log.
* Envelope strip preserves only {type, payload, correlation_id, published_at}.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.bus.envelope import MessageEnvelope
from packages.core import CorrelationId
from services.analytics_api.app.models.events import EventType
from services.analytics_api.app.sse import (
    ClientHandle,
    SSEConnectionLimitError,
    SSEMultiplexer,
    _envelope_to_sse_event,
    _filter_for_type,
    _subjects_for_types,
)

_T_NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


def _make_envelope(*, payload: dict[str, Any]) -> MessageEnvelope:
    return MessageEnvelope(
        correlation_id=CorrelationId("cid-test"),
        publisher="execution",
        payload=payload,
        published_at=_T_NOW,
    )


def _make_bus_mock() -> MagicMock:
    bus = MagicMock()
    bus.subscribe = AsyncMock()

    sub_factory_count = [0]

    async def _subscribe(_subject: str, _handler: Any) -> Any:
        sub_factory_count[0] += 1
        sub = MagicMock(name=f"sub-{sub_factory_count[0]}")
        sub.unsubscribe = AsyncMock()
        return sub

    bus.subscribe.side_effect = _subscribe
    return bus


def _make_multiplexer(
    *,
    bus: MagicMock | None = None,
    max_connections: int = 50,
    queue_maxsize: int = 1000,
    overflow_log_interval_s: int = 60,
) -> SSEMultiplexer:
    return SSEMultiplexer(
        bus=bus or _make_bus_mock(),
        logger=MagicMock(),
        heartbeat_interval_s=15,
        client_queue_maxsize=queue_maxsize,
        max_connections=max_connections,
        overflow_log_interval_s=overflow_log_interval_s,
    )


# ---------------------------------------------------------------------------
# subjects_for_types — subject deduplication
# ---------------------------------------------------------------------------


def test_subjects_for_types_dedups_orders_events() -> None:
    """POSITIONS + TRADES share `orders.events.>` — single subscription, both types."""
    types = frozenset({EventType.POSITIONS, EventType.TRADES})
    subjects = _subjects_for_types(types)
    assert "orders.events.>" in subjects
    assert set(subjects["orders.events.>"]) == {EventType.POSITIONS, EventType.TRADES}
    # No other subjects when only positions+trades requested.
    assert set(subjects.keys()) == {"orders.events.>"}


def test_subjects_for_types_distinct_subjects_for_unrelated() -> None:
    types = frozenset({EventType.SIGNALS, EventType.ALERTS})
    subjects = _subjects_for_types(types)
    assert subjects == {
        "signals.validated": [EventType.SIGNALS],
        "system.alerts": [EventType.ALERTS],
    }


def test_subjects_for_types_all_five() -> None:
    types = frozenset(EventType)
    subjects = _subjects_for_types(types)
    assert set(subjects.keys()) == {
        "orders.events.>",
        "signals.validated",
        "signals.rejected.>",
        "system.alerts",
    }


# ---------------------------------------------------------------------------
# register_client / connection cap
# ---------------------------------------------------------------------------


async def test_register_client_creates_subscriptions_for_types() -> None:
    bus = _make_bus_mock()
    mux = _make_multiplexer(bus=bus)
    handle = await mux.register_client(frozenset({EventType.SIGNALS, EventType.ALERTS}))
    assert isinstance(handle, ClientHandle)
    assert handle.types == frozenset({EventType.SIGNALS, EventType.ALERTS})
    assert mux.active_client_count == 1
    assert bus.subscribe.await_count == 2  # signals.validated + system.alerts


async def test_register_client_dedups_shared_subject() -> None:
    """POSITIONS + TRADES → 1 subscribe call (orders.events.> only)."""
    bus = _make_bus_mock()
    mux = _make_multiplexer(bus=bus)
    await mux.register_client(frozenset({EventType.POSITIONS, EventType.TRADES}))
    assert bus.subscribe.await_count == 1


async def test_max_connections_cap_raises() -> None:
    """N+1th register_client raises SSEConnectionLimitError."""
    mux = _make_multiplexer(max_connections=2)
    await mux.register_client(frozenset({EventType.SIGNALS}))
    await mux.register_client(frozenset({EventType.SIGNALS}))
    with pytest.raises(SSEConnectionLimitError):
        await mux.register_client(frozenset({EventType.SIGNALS}))
    assert mux.active_client_count == 2


# ---------------------------------------------------------------------------
# unregister_client / shutdown idempotency
# ---------------------------------------------------------------------------


async def test_unregister_client_drains_subscriptions_and_is_idempotent() -> None:
    """WG#4 — second unregister_client call is no-op."""
    bus = _make_bus_mock()
    mux = _make_multiplexer(bus=bus)
    handle = await mux.register_client(frozenset({EventType.SIGNALS}))
    initial_active = handle.is_active
    assert initial_active is True

    await mux.unregister_client(handle)
    # Re-read attribute (avoid mypy narrowing on the original assertion).
    assert handle.is_active is False
    assert mux.active_client_count == 0
    # NATS unsubscribe called exactly once. unsubscribe is an AsyncMock — cast
    # because mypy sees Subscription.unsubscribe as the runtime Callable type.
    sub_unsubscribe = cast("AsyncMock", handle.subscriptions[0].unsubscribe)
    assert sub_unsubscribe.await_count == 1

    # Idempotency pin: second call returns early, no new unsubscribe.
    await mux.unregister_client(handle)
    assert sub_unsubscribe.await_count == 1
    assert mux.active_client_count == 0


async def test_unregister_client_posts_sentinel_to_queue() -> None:
    """Generator wakes up on sentinel posted by unregister."""
    mux = _make_multiplexer()
    handle = await mux.register_client(frozenset({EventType.SIGNALS}))

    await mux.unregister_client(handle)
    # Sentinel = None should be in the queue.
    item = await asyncio.wait_for(handle.queue.get(), timeout=0.5)
    assert item is None


async def test_shutdown_iterates_active_clients_and_is_idempotent() -> None:
    """WG#5 — shutdown drains all handles via snapshot iteration; second call no-op."""
    mux = _make_multiplexer()
    handles = [await mux.register_client(frozenset({EventType.SIGNALS})) for _ in range(3)]
    assert mux.active_client_count == 3

    await mux.shutdown()
    assert mux.active_client_count == 0
    for h in handles:
        assert h.is_active is False
        # Sentinel posted to each.
        item = await asyncio.wait_for(h.queue.get(), timeout=0.5)
        assert item is None

    # Second shutdown is no-op (no exception, no spurious work).
    await mux.shutdown()
    assert mux.active_client_count == 0


# ---------------------------------------------------------------------------
# Filter logic — POSITIONS (close + sl_moved) vs TRADES (placed + filled)
# ---------------------------------------------------------------------------


def test_handler_filters_positions_includes_sl_moved_and_order_closed() -> None:
    """CONCERN #3 — POSITIONS whitelist = {order_closed, sl_moved}."""
    env_close = _make_envelope(payload={"event_type": "order_closed"})
    env_sl = _make_envelope(payload={"event_type": "sl_moved"})
    env_filled = _make_envelope(payload={"event_type": "order_filled"})
    env_placed = _make_envelope(payload={"event_type": "order_placed"})
    assert _filter_for_type(env_close, EventType.POSITIONS) is True
    assert _filter_for_type(env_sl, EventType.POSITIONS) is True
    # Trades-side events are NOT positions.
    assert _filter_for_type(env_filled, EventType.POSITIONS) is False
    assert _filter_for_type(env_placed, EventType.POSITIONS) is False


def test_handler_filters_trades_includes_placed_and_filled() -> None:
    """TRADES whitelist = {order_placed, order_filled}; disjoint with POSITIONS."""
    env_placed = _make_envelope(payload={"event_type": "order_placed"})
    env_filled = _make_envelope(payload={"event_type": "order_filled"})
    env_close = _make_envelope(payload={"event_type": "order_closed"})
    env_sl = _make_envelope(payload={"event_type": "sl_moved"})
    assert _filter_for_type(env_placed, EventType.TRADES) is True
    assert _filter_for_type(env_filled, EventType.TRADES) is True
    assert _filter_for_type(env_close, EventType.TRADES) is False
    assert _filter_for_type(env_sl, EventType.TRADES) is False


def test_handler_filter_pass_through_for_non_shared_subjects() -> None:
    """SIGNALS/SCORING/ALERTS are 1:1 subject → type, no filter."""
    env = _make_envelope(payload={"anything": "yes"})
    assert _filter_for_type(env, EventType.SIGNALS) is True
    assert _filter_for_type(env, EventType.SCORING) is True
    assert _filter_for_type(env, EventType.ALERTS) is True


# ---------------------------------------------------------------------------
# Drop-oldest queue overflow
# ---------------------------------------------------------------------------


def test_handler_drop_oldest_on_queue_full() -> None:
    """OQ-3=A — saturate queue → next put drops oldest, overflow_count increments."""
    mux = _make_multiplexer(queue_maxsize=2, overflow_log_interval_s=0)
    handle = ClientHandle(
        client_id="test",
        types=frozenset({EventType.SIGNALS}),
        queue=asyncio.Queue(maxsize=2),
    )

    mux._enqueue_with_overflow(handle, {"id": 1})
    mux._enqueue_with_overflow(handle, {"id": 2})
    assert handle.queue.qsize() == 2
    assert handle.overflow_count == 0

    # Overflow.
    mux._enqueue_with_overflow(handle, {"id": 3})
    assert handle.queue.qsize() == 2
    assert handle.overflow_count == 1

    # Oldest dropped: queue should now contain ids 2 then 3.
    drained: list[Any] = []
    while not handle.queue.empty():
        drained.append(handle.queue.get_nowait())
    assert [d["id"] for d in drained] == [2, 3]


# ---------------------------------------------------------------------------
# envelope → SSE event mapping
# ---------------------------------------------------------------------------


def test_envelope_to_sse_event_strips_internal_fields() -> None:
    """OQ-5=A — output dict has only {type, payload, correlation_id, published_at}."""
    envelope = _make_envelope(payload={"event_type": "order_closed", "x": 1})
    out = _envelope_to_sse_event(envelope, EventType.POSITIONS)
    assert set(out.keys()) == {"type", "payload", "correlation_id", "published_at"}
    assert out["type"] == "positions"
    assert out["payload"] == {"event_type": "order_closed", "x": 1}
    assert out["correlation_id"] == "cid-test"
    assert out["published_at"] == _T_NOW.isoformat()
    # Internal envelope fields stripped.
    assert "message_id" not in out
    assert "publisher" not in out
    assert "schema_version" not in out
