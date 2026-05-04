"""SSE multiplexer: per-connection NATS subscriptions + per-client async queue (T-408).

Lifespan-attached singleton (``app.state.sse_multiplexer``). Each SSE client
connection calls :meth:`SSEMultiplexer.register_client` to allocate:

* per-client :class:`asyncio.Queue` (maxsize from settings; OQ-3=A drop-oldest)
* per-client NATS subscriptions (one per unique subject; some types share
  subjects — e.g., ``orders.events.>`` serves both POSITIONS and TRADES with
  disjoint event_type filters)
* incremented active-client counter (rejected at ``max_connections``; OQ-7=A)

On disconnect / shutdown: :meth:`SSEMultiplexer.unregister_client` drains
NATS subs + posts :data:`_QUEUE_SENTINEL` so any awaiting generator wakes up
and breaks out of its loop. Idempotent — second call is a no-op (gated by
:attr:`ClientHandle.is_active`).

:meth:`SSEMultiplexer.shutdown` (lifespan teardown BEFORE ``bus.close()``)
iterates a snapshot of active handles and unregisters each.

Hexagonal split (§N7): this module owns subscription mgmt + per-client
queueing + filtering. The :mod:`services.analytics_api.app.routers.events`
router owns the StreamingResponse generator that consumes the queue and
formats SSE wire bytes.

WG references (T-408 plan-reviewer APPROVE pass-2):

* WG#2 — sentinel typing pin (``_QUEUE_SENTINEL: Final[None] = None``;
  ``ClientHandle.queue: asyncio.Queue[dict[str, Any] | None]``).
* WG#3 — counter atomicity invariant comment (no ``await`` between check
  and ``+=`` in :meth:`register_client`; mirrored in
  :meth:`unregister_client`).
* WG#4 — idempotency guard ordering: ``is_active`` flag flipped FIRST,
  then NATS unsubscribes, then sentinel, then counter decrement.
* WG#5 — :meth:`shutdown` snapshot-iteration via ``list(self._active_handles)``.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from packages.bus.envelope import (
    MessageEnvelope,  # noqa: TC001 — runtime use in _envelope_to_sse_event / handler closures
)

from .models.events import EventType

if TYPE_CHECKING:
    from collections.abc import Callable

    from nats.aio.subscription import Subscription
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient

__all__ = [
    "ClientHandle",
    "SSEConnectionLimitError",
    "SSEMultiplexer",
]


# Sentinel value posted to client queues by unregister_client / shutdown to
# break the StreamingResponse generator loop cleanly. Distinct from any real
# event payload (events are dicts) — the queue is typed
# ``asyncio.Queue[dict[str, Any] | None]`` so static checkers see the union.
_QUEUE_SENTINEL: Final[None] = None


# event_type whitelist sets per type; explicit constants for L-008 spirit
# (no string scattering across functions; single source of truth).
_POSITIONS_EVENT_TYPES: frozenset[str] = frozenset({"order_closed", "sl_moved"})
_TRADES_EVENT_TYPES: frozenset[str] = frozenset({"order_placed", "order_filled"})


class SSEConnectionLimitError(Exception):
    """Raised by :meth:`SSEMultiplexer.register_client` when ``max_connections`` reached.

    Router catches and maps to HTTP 503 with detail message containing the
    configured ``max_connections`` value.
    """


@dataclass(slots=True, eq=False)
class ClientHandle:
    """Per-client state: id + types + queue + subscriptions + counters.

    Returned by :meth:`SSEMultiplexer.register_client`; consumed by router's
    StreamingResponse generator. ``is_active`` flag gates idempotent
    :meth:`SSEMultiplexer.unregister_client` (toggled to ``False`` on first
    unregister; second call returns immediately).
    """

    client_id: str
    types: frozenset[EventType]
    queue: asyncio.Queue[dict[str, Any] | None]
    subscriptions: list[Subscription] = field(default_factory=list)
    overflow_count: int = 0
    last_overflow_log_monotonic: float = 0.0
    is_active: bool = True


def _envelope_to_sse_event(envelope: MessageEnvelope, event_type: EventType) -> dict[str, Any]:
    """Strip envelope to ``{type, payload, correlation_id, published_at}`` per OQ-5=A.

    Drops ``message_id``, ``schema_version``, ``publisher`` (UI doesn't need).
    Preserves ``correlation_id`` (UI debug) + ``published_at`` (ordering).
    """
    return {
        "type": event_type.value,
        "payload": envelope.payload,
        "correlation_id": envelope.correlation_id,
        "published_at": envelope.published_at.isoformat(),
    }


def _filter_for_type(envelope: MessageEnvelope, event_type: EventType) -> bool:
    """Server-side discriminator filter for shared subjects.

    POSITIONS emits on ``event_type IN ('order_closed', 'sl_moved')`` — close
    drives position-card removal; sl_moved drives current-SL indicator update.
    TRADES emits on ``event_type IN ('order_placed', 'order_filled')`` — fill
    lifecycle for trade explorer feed. Disjoint sets: a single client
    subscribed to both POSITIONS and TRADES receives each ``orders.events.>``
    payload at most once. Other types are 1:1 subject → type, no filter.
    """
    if event_type is EventType.POSITIONS:
        return envelope.payload.get("event_type") in _POSITIONS_EVENT_TYPES
    if event_type is EventType.TRADES:
        return envelope.payload.get("event_type") in _TRADES_EVENT_TYPES
    return True


def _subjects_for_types(types: frozenset[EventType]) -> dict[str, list[EventType]]:
    """Compute unique NATS subjects + which types each subject feeds.

    Returns ``{subject: [types]}``. POSITIONS + TRADES share
    ``orders.events.>`` — handler dispatches to both type queues
    server-side via :func:`_filter_for_type`.
    """
    out: dict[str, list[EventType]] = {}
    shared_orders: list[EventType] = []
    if EventType.POSITIONS in types:
        shared_orders.append(EventType.POSITIONS)
    if EventType.TRADES in types:
        shared_orders.append(EventType.TRADES)
    if shared_orders:
        out["orders.events.>"] = shared_orders
    if EventType.SIGNALS in types:
        out["signals.validated"] = [EventType.SIGNALS]
    if EventType.SCORING in types:
        out["signals.rejected.>"] = [EventType.SCORING]
    if EventType.ALERTS in types:
        out["system.alerts"] = [EventType.ALERTS]
    return out


class SSEMultiplexer:
    """Lifespan-owned singleton managing per-connection NATS fan-out.

    All 4 tuning knobs flow through DI from :class:`Settings` (per BLOCKER #1
    fix + L-001 active control). NO module-level constants for these values.
    """

    def __init__(
        self,
        *,
        bus: NatsClient,
        logger: BoundLogger,
        heartbeat_interval_s: int,
        client_queue_maxsize: int,
        max_connections: int,
        overflow_log_interval_s: int,
    ) -> None:
        self._bus = bus
        self._logger = logger
        self._heartbeat_interval_s = heartbeat_interval_s
        self._client_queue_maxsize = client_queue_maxsize
        self._max_connections = max_connections
        self._overflow_log_interval_s = overflow_log_interval_s
        self._active_handles: set[ClientHandle] = set()
        self._active_count: int = 0

    @property
    def heartbeat_interval_s(self) -> int:
        return self._heartbeat_interval_s

    @property
    def max_connections(self) -> int:
        return self._max_connections

    @property
    def active_client_count(self) -> int:
        return self._active_count

    async def register_client(self, types: frozenset[EventType]) -> ClientHandle:
        """Allocate per-client queue + per-type NATS subscriptions.

        Raises :class:`SSEConnectionLimitError` if ``active_count >= max``.
        Raises any subscribe failure after rolling back partial subscriptions.
        """
        # WG#3: Single asyncio loop invariant — no `await` between check and
        # increment, so this sequence is atomic without asyncio.Lock. Decrement
        # in unregister_client gated by handle.is_active flag (idempotency).
        if self._active_count >= self._max_connections:
            raise SSEConnectionLimitError(f"max SSE connections reached ({self._max_connections})")
        self._active_count += 1

        client_id = str(uuid.uuid4())
        handle = ClientHandle(
            client_id=client_id,
            types=types,
            queue=asyncio.Queue(maxsize=self._client_queue_maxsize),
        )

        subjects = _subjects_for_types(types)
        try:
            for subject, subject_types in subjects.items():
                handler = self._make_handler(handle, subject_types)
                sub = await self._bus.subscribe(subject, handler)
                handle.subscriptions.append(sub)
        except Exception:
            # Roll back partial subscriptions before re-raising; keep the
            # exception type so the router can decide whether to surface 500.
            for sub in handle.subscriptions:
                try:
                    await sub.unsubscribe()
                except Exception as rollback_exc:
                    self._logger.warning(
                        "sse_rollback_unsubscribe_failed",
                        error=str(rollback_exc),
                    )
            self._active_count -= 1
            raise

        self._active_handles.add(handle)
        self._logger.info(
            "sse_client_connected",
            client_id=client_id,
            types=[t.value for t in sorted(types, key=lambda x: x.value)],
            active_count=self._active_count,
        )
        return handle

    async def unregister_client(self, handle: ClientHandle) -> None:
        """Drain NATS subscriptions + post sentinel + decrement counter.

        IDEMPOTENT — second call returns immediately (``is_active`` guard).
        Required idempotency: router's ``finally`` block calls this AND
        :meth:`shutdown` calls this; double-call is safe.
        """
        # WG#4: idempotency guard FIRST — flip flag before any await so a
        # concurrent shutdown call sees the flag and short-circuits.
        if not handle.is_active:
            return
        handle.is_active = False

        for sub in handle.subscriptions:
            try:
                await sub.unsubscribe()
            except Exception as exc:
                self._logger.warning(
                    "sse_unsubscribe_failed",
                    client_id=handle.client_id,
                    error=str(exc),
                )

        # Post sentinel to wake any generator stuck on `queue.get()`.
        # Never await — if QueueFull, drop oldest first then retry put.
        try:
            handle.queue.put_nowait(_QUEUE_SENTINEL)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                handle.queue.get_nowait()
            handle.queue.put_nowait(_QUEUE_SENTINEL)

        # WG#3 mirror: no `await` between is_active check (top of method) and
        # this decrement — single asyncio loop guarantees atomicity.
        self._active_handles.discard(handle)
        self._active_count -= 1

        self._logger.info(
            "sse_client_disconnected",
            client_id=handle.client_id,
            active_count=self._active_count,
            overflow_count=handle.overflow_count,
        )

    async def shutdown(self) -> None:
        """Drain all active client subscriptions before bus.close().

        Iterates a SNAPSHOT of active handles per WG#5 (set is mutated by
        :meth:`unregister_client`). IDEMPOTENT — second call is no-op (active
        set is empty after first call drains). Each generator wakes up on
        sentinel, breaks loop, finally block calls unregister_client (no-op
        per ``is_active=False`` guard).
        """
        # WG#5: snapshot copy before iteration; unregister_client mutates set.
        for handle in list(self._active_handles):
            await self.unregister_client(handle)

    def _make_handler(
        self,
        handle: ClientHandle,
        subject_types: list[EventType],
    ) -> Callable[[MessageEnvelope], Any]:
        """Build a NATS handler closure for one subject + its applicable types.

        Handler iterates each applicable type, applies :func:`_filter_for_type`,
        and enqueues the SSE-mapped event. Drop-oldest on QueueFull per OQ-3=A.
        """

        async def _handler(envelope: MessageEnvelope) -> None:
            for event_type in subject_types:
                if not _filter_for_type(envelope, event_type):
                    continue
                event = _envelope_to_sse_event(envelope, event_type)
                self._enqueue_with_overflow(handle, event)

        return _handler

    def _enqueue_with_overflow(self, handle: ClientHandle, event: dict[str, Any]) -> None:
        """Put event on queue; drop oldest on QueueFull + rate-limited overflow log."""
        try:
            handle.queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            # Drop oldest then retry. asyncio.Queue is single-threaded; no race.
            with contextlib.suppress(asyncio.QueueEmpty):
                handle.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                # Pathological — sentinel + drop racing? swallow, increment counter.
                handle.queue.put_nowait(event)
        handle.overflow_count += 1

        # Rate-limited warning log — at most one per overflow_log_interval_s
        # per client.
        now_mono = time.monotonic()
        if now_mono - handle.last_overflow_log_monotonic >= self._overflow_log_interval_s:
            handle.last_overflow_log_monotonic = now_mono
            self._logger.warning(
                "sse_client_buffer_overflow",
                client_id=handle.client_id,
                overflow_count=handle.overflow_count,
                queue_maxsize=self._client_queue_maxsize,
            )
