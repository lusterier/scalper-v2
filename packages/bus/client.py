"""NATS JetStream client wrapper (§8, §5.7, §5.8).

This module intentionally uses an asymmetric pub/sub split:

* :meth:`NatsClient.publish` goes through **JetStream**
  (``js.publish``). Every subject in §8.1 maps to a stream in §8.2,
  and the ``Nats-Msg-Id`` header is meaningful only on JS publishes
  against streams with ``duplicate_window`` set. JS publish returns a
  :class:`~nats.js.api.PubAck`, guaranteeing the message is durably
  on-stream before control returns to the caller.

* :meth:`NatsClient.subscribe` uses **core NATS** (``nc.subscribe``)
  and is ephemeral — no ack, no durable consumer, no replay, no
  dedup. Handler exceptions are logged as ``bus_handler_failed`` and
  swallowed.

The asymmetry is intentional: writes need durability immediately,
durable reads land with hazard **H-009** (§20) as a separate class
(likely ``DurableJsConsumer``). This client does not evolve to support
durable/ack semantics.

Log event catalog (all structured facts, no message-field
interpolation per §5.7):

* ``bus_connect_started`` / ``bus_connected`` / ``bus_connect_failed``
* ``bus_disconnected`` / ``bus_reconnected``
* ``bus_closing`` / ``bus_closed`` / ``bus_drain_failed``
* ``bus_published`` / ``bus_publish_failed`` /
  ``bus_publish_deduplicated``
* ``bus_subscribed`` / ``bus_message_received`` /
  ``bus_handler_failed``
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TYPE_CHECKING

import nats
import nats.errors
import nats.js.errors

from packages.core import idempotent, non_idempotent

from .envelope import MessageEnvelope
from .errors import NotConnectedError, PublishError, SubscribeError

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsConnection
    from nats.aio.msg import Msg
    from nats.aio.subscription import Subscription
    from nats.js.client import JetStreamContext
    from structlog.stdlib import BoundLogger

__all__ = ["ConnectionState", "NatsClient"]

Handler = Callable[[MessageEnvelope], Awaitable[None]]


class ConnectionState(Enum):
    """Lifecycle states for :class:`NatsClient`.

    Transitions: ``DISCONNECTED → CONNECTING → CONNECTED → CLOSING →
    CLOSED``. ``connect`` is legal only from ``DISCONNECTED``;
    ``publish`` / ``subscribe`` require ``CONNECTED``; ``close`` is a
    no-op from ``DISCONNECTED`` / ``CLOSED`` and raises from
    ``CONNECTING``.
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"


class NatsClient:
    """Thin async wrapper around ``nats-py`` (§8).

    ``logger`` is required — the bus package has no service identity of
    its own; callers inject ``get_logger("<service>", "system")``.

    ``max_reconnect_attempts=-1`` overrides the ``nats-py`` default of
    60 and means "retry forever", matching the long-lived-service
    assumption in §3.3.
    """

    def __init__(
        self,
        *,
        servers: list[str],
        name: str,
        logger: BoundLogger,
        max_reconnect_attempts: int = -1,
        reconnect_time_wait: float = 2.0,
    ) -> None:
        self._servers = servers
        self._name = name
        self._logger = logger
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_time_wait = reconnect_time_wait
        self._state = ConnectionState.DISCONNECTED
        self._nc: NatsConnection | None = None
        self._js: JetStreamContext | None = None
        self._subscriptions: list[Subscription] = []

    @property
    def state(self) -> ConnectionState:
        """Current lifecycle state; used by readiness probes."""
        return self._state

    async def connect(self) -> None:
        """Open the NATS connection and acquire a JetStream context.

        Legal only from :attr:`ConnectionState.DISCONNECTED`. On
        failure the state resets to ``DISCONNECTED`` and the original
        exception propagates.
        """
        if self._state is not ConnectionState.DISCONNECTED:
            raise NotConnectedError(
                f"connect called in state {self._state.value!r}; expected 'disconnected'"
            )
        self._state = ConnectionState.CONNECTING
        self._logger.info("bus_connect_started", servers=self._servers)
        try:
            self._nc = await nats.connect(
                servers=self._servers,
                name=self._name,
                max_reconnect_attempts=self._max_reconnect_attempts,
                reconnect_time_wait=self._reconnect_time_wait,
                disconnected_cb=self._on_disconnected,
                reconnected_cb=self._on_reconnected,
            )
        except Exception as exc:
            # any failure in nats.connect() must reset state before propagating;
            # type narrowing on nats exceptions isn't worth it here
            self._state = ConnectionState.DISCONNECTED
            self._logger.error("bus_connect_failed", error=str(exc))
            raise
        self._js = self._nc.jetstream()
        self._state = ConnectionState.CONNECTED
        self._logger.info("bus_connected", servers=self._servers)

    async def close(self) -> None:
        """Drain tracked subscriptions, then close the NATS connection.

        No-op from ``DISCONNECTED`` / ``CLOSED``. Raises from
        ``CONNECTING`` — caller must let ``connect`` resolve first.
        A failed ``drain()`` on one subscription is logged and does not
        block the remaining drains or ``nc.close()``.
        """
        if self._state in (ConnectionState.DISCONNECTED, ConnectionState.CLOSED):
            return
        if self._state is not ConnectionState.CONNECTED:
            raise NotConnectedError(
                f"close called in state {self._state.value!r}; let connect() resolve first"
            )
        self._state = ConnectionState.CLOSING
        self._logger.info("bus_closing", subscription_count=len(self._subscriptions))
        for sub in self._subscriptions:
            try:
                await sub.drain()  # type: ignore[no-untyped-call]  # nats-py Subscription.drain is untyped
            except Exception as exc:
                # one bad drain must not block other drains or nc.close()
                self._logger.warning("bus_drain_failed", error=str(exc))
        self._subscriptions.clear()
        if self._nc is not None:
            await self._nc.close()
        self._state = ConnectionState.CLOSED
        self._logger.info("bus_closed")

    @non_idempotent
    async def publish(self, subject: str, envelope: MessageEnvelope) -> None:
        """Publish ``envelope`` to ``subject`` via JetStream.

        Sets the ``Nats-Msg-Id`` header to ``str(envelope.message_id)``
        so the server dedups within the stream's ``duplicate_window``
        (§8.2). If the server reports a duplicate, logs
        ``bus_publish_deduplicated`` at DEBUG and returns normally —
        the message is on the stream exactly once.

        Marked ``@non_idempotent`` at the wrapper boundary: the wrapper
        cannot know whether the target stream has ``duplicate_window``
        configured, so callers must treat publish as
        not-safe-to-retry until they own that configuration.

        Raises :class:`NotConnectedError` outside ``CONNECTED``.
        Wraps any :class:`nats.errors.Error` in :class:`PublishError`
        with the original as ``__cause__``.
        """
        if self._state is not ConnectionState.CONNECTED or self._js is None:
            raise NotConnectedError(f"publish called in state {self._state.value!r}")
        payload = envelope.to_bytes()
        headers = {"Nats-Msg-Id": str(envelope.message_id)}
        try:
            ack = await self._js.publish(subject, payload, headers=headers)
        except nats.errors.Error as exc:
            self._logger.error(
                "bus_publish_failed",
                subject=subject,
                error=str(exc),
                correlation_id=envelope.correlation_id,
            )
            raise PublishError(f"publish to {subject!r} failed") from exc
        if ack.duplicate:
            self._logger.debug(
                "bus_publish_deduplicated",
                subject=subject,
                message_id=str(envelope.message_id),
                correlation_id=envelope.correlation_id,
            )
        else:
            self._logger.debug(
                "bus_published",
                subject=subject,
                message_id=str(envelope.message_id),
                correlation_id=envelope.correlation_id,
                payload_bytes=len(payload),
            )

    async def subscribe(self, subject: str, handler: Handler) -> Subscription:
        """Subscribe to ``subject`` with an ephemeral core-NATS consumer.

        The returned :class:`~nats.aio.subscription.Subscription` is
        also tracked internally and drained by :meth:`close`. Handler
        exceptions (including envelope-parse failures) are logged as
        ``bus_handler_failed`` and swallowed, so one bad message never
        cancels the subscription.

        Raises :class:`NotConnectedError` outside ``CONNECTED``. Wraps
        any :class:`nats.errors.Error` from the underlying subscribe
        call in :class:`SubscribeError` with the original as
        ``__cause__``.
        """
        if self._state is not ConnectionState.CONNECTED or self._nc is None:
            raise NotConnectedError(f"subscribe called in state {self._state.value!r}")

        async def _dispatch(msg: Msg) -> None:
            try:
                envelope = MessageEnvelope.from_bytes(msg.data)
            except Exception as exc:
                # swallow: one bad message must not kill the subscription;
                # caller observes via bus_handler_failed logs
                self._logger.error(
                    "bus_handler_failed",
                    subject=msg.subject,
                    error=f"envelope_parse_failed: {exc}",
                )
                return
            self._logger.debug(
                "bus_message_received",
                subject=msg.subject,
                message_id=str(envelope.message_id),
                correlation_id=envelope.correlation_id,
            )
            try:
                await handler(envelope)
            except Exception as exc:
                # swallow: one bad message must not kill the subscription;
                # caller observes via bus_handler_failed logs
                self._logger.error(
                    "bus_handler_failed",
                    subject=msg.subject,
                    error=str(exc),
                    correlation_id=envelope.correlation_id,
                )

        try:
            sub = await self._nc.subscribe(subject, cb=_dispatch)
        except nats.errors.Error as exc:
            raise SubscribeError(f"subscribe to {subject!r} failed") from exc
        self._subscriptions.append(sub)
        self._logger.info("bus_subscribed", subject=subject)
        return sub

    @idempotent
    async def kv_get(self, bucket: str, key: str) -> tuple[bytes, int] | None:
        """Read ``value`` + revision from JetStream KV ``bucket`` under ``key``.

        Returns ``(value, revision)`` if the key exists; returns ``None``
        if the key was never set (``KeyNotFoundError``). Caller treats
        ``None`` as fresh state — for the rate limiter (T-205) this means
        a sub-account bucket starts at full capacity.

        Idempotent (§N3): reads are by definition replay-safe.

        Bucket must be pre-provisioned by infra (T-019 nats-bootstrap or
        F2+ pre-flight check at execution-service startup) — symmetric
        with :meth:`kv_put`. A missing bucket raises
        :class:`PublishError` (config bug, fail loud per §0.4).

        Raises :class:`NotConnectedError` outside ``CONNECTED``. Wraps
        non-``KeyNotFoundError`` :class:`nats.errors.Error` as
        :class:`PublishError` with the original as ``__cause__``.
        """
        if self._state is not ConnectionState.CONNECTED or self._js is None:
            raise NotConnectedError(f"kv_get called in state {self._state.value!r}")
        try:
            kv = await self._js.key_value(bucket)
            entry = await kv.get(key)
        except nats.js.errors.KeyNotFoundError:
            return None
        except nats.errors.Error as exc:
            self._logger.error(
                "bus_kv_get_failed",
                bucket=bucket,
                key=key,
                error=str(exc),
            )
            raise PublishError(f"kv_get from {bucket!r}/{key!r} failed") from exc
        # nats-py Entry.value can be None for tombstone-deleted keys; treat
        # the same as KeyNotFoundError per ADR-0003 fail-safe (caller sees a
        # fresh-state cue).
        if entry.value is None or entry.revision is None:
            return None
        self._logger.debug(
            "bus_kv_get",
            bucket=bucket,
            key=key,
            revision=entry.revision,
        )
        return (entry.value, entry.revision)

    @non_idempotent
    async def kv_update(
        self,
        bucket: str,
        key: str,
        value: bytes,
        last_revision: int,
    ) -> int:
        """CAS update: write ``value`` only if the current revision matches.

        Calls NATS KV ``update(key, value, last=last_revision)``; raises
        :class:`PublishError` (with the original
        :class:`nats.errors.Error` as ``__cause__``) on revision
        mismatch. Caller (T-205 rate limiter) re-reads + retries on
        conflict per ADR-0003 §3.

        Non-idempotent (§N3) per CAS semantics: replaying with the same
        ``last_revision`` after a successful first call will fail
        because the revision has advanced — the second call is NOT a
        no-op of the first.

        Returns the new revision on success. Bucket must be
        pre-provisioned.
        """
        if self._state is not ConnectionState.CONNECTED or self._js is None:
            raise NotConnectedError(f"kv_update called in state {self._state.value!r}")
        try:
            kv = await self._js.key_value(bucket)
            revision = await kv.update(key, value, last=last_revision)
        except nats.errors.Error as exc:
            self._logger.debug(
                "bus_kv_update_failed",
                bucket=bucket,
                key=key,
                last_revision=last_revision,
                error=str(exc),
            )
            raise PublishError(
                f"kv_update to {bucket!r}/{key!r} (last={last_revision}) failed"
            ) from exc
        self._logger.debug(
            "bus_kv_update",
            bucket=bucket,
            key=key,
            revision=revision,
            last_revision=last_revision,
            value_bytes=len(value),
        )
        return revision

    @idempotent
    async def kv_put(self, bucket: str, key: str, value: bytes) -> int:
        """Write ``value`` to JetStream KV ``bucket`` under ``key``.

        Idempotent (§N3): NATS KV PUT replaces by key — last-write-wins
        on ``(bucket, key)``. Same ``(bucket, key, value)`` yields the
        same final KV state regardless of call count. JS returns a
        monotonically increasing revision number which is surfaced for
        callers that want to assert ordering or log-correlate; callers
        that don't care can ignore the return value.

        Bucket must be pre-provisioned by infra (T-012
        ``infra/nats/streams.yaml``). A missing bucket raises
        :class:`PublishError` (config bug, fail loud per §0.4) — the
        bus does not auto-create.

        Raises :class:`NotConnectedError` outside ``CONNECTED``.
        Wraps any :class:`nats.errors.Error` (including bucket-missing)
        in :class:`PublishError` with the original as ``__cause__``.
        """
        if self._state is not ConnectionState.CONNECTED or self._js is None:
            raise NotConnectedError(f"kv_put called in state {self._state.value!r}")
        try:
            kv = await self._js.key_value(bucket)
            revision = await kv.put(key, value)
        except nats.errors.Error as exc:
            self._logger.error(
                "bus_kv_put_failed",
                bucket=bucket,
                key=key,
                error=str(exc),
            )
            raise PublishError(f"kv_put to {bucket!r}/{key!r} failed") from exc
        self._logger.debug(
            "bus_kv_put",
            bucket=bucket,
            key=key,
            revision=revision,
            value_bytes=len(value),
        )
        return revision

    async def _on_disconnected(self) -> None:
        self._logger.warning("bus_disconnected")

    async def _on_reconnected(self) -> None:
        self._logger.info("bus_reconnected", servers=self._servers)
