"""OutboxRelayWorker — polls ``outbox_events`` and publishes to NATS (T-537a2).

Consumer of T-537a1 outbox base infra (queries + types + migration 0016).
Hosted in service lifespan as ``asyncio.create_task(worker.run())``.

Transaction & lock semantics — Variant B (batch-level tx):

* ``async with pool.acquire() as conn, conn.transaction():`` wraps the
  entire batch (select_pending + per-event publish + per-event mark_*).
* ``select_pending_outbox_events`` (T-537a1) uses ``FOR UPDATE SKIP LOCKED``
  → batch rows are locked through publish-and-mark; other replicas
  SKIP_LOCKED them.
* Per-event SERIAL publish (OQ-1 2026-05-09; preserves NATS subject FIFO
  per service). Failures generate ``mark_failed`` writes; successes
  generate ``mark_published`` writes — both commit together at batch tx
  exit.
* ``asyncio.CancelledError`` propagates UP UNCAUGHT (per-event try/except
  catches ``Exception`` only, NOT ``BaseException``) → batch tx rolls
  back; rows return to pending state with original ``attempt_count``;
  next poll picks them up. Silent cancel (OQ-2 2026-05-09).

Shutdown ordering contract (T-537b lifespan integration will follow):

  1. ``await worker.stop()``  — cancel relay task + drain in-flight tx
  2. ``await bus.close()``    — NATS unsubscribe + connection close
  3. ``await pool.close()``   — asyncpg pool drain

Steps 2-3 run AFTER ``stop()`` so any in-flight ``bus.publish`` call
inside the relay completes (or rolls back via cancel) BEFORE bus
disappears; ``pool.close()`` is last because the relay's tx wrapping
the publish needs the pool alive until cancellation propagates.

Logger keys (5 module-level Final[str] constants + frozenset registry).
Tests pin verbatim string values.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from packages.bus.envelope import MessageEnvelope
from packages.core import CorrelationId

from .queries import (
    mark_outbox_event_failed,
    mark_outbox_event_published,
    select_pending_outbox_events,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import BusProtocol

    from .types import OutboxRelaySettings

__all__ = [
    "LOG_EXHAUSTED",
    "LOG_POLL_STARTED",
    "LOG_PUBLISH_FAILED",
    "LOG_PUBLISH_SUCCEEDED",
    "LOG_STOPPED",
    "OutboxRelayWorker",
]


LOG_POLL_STARTED: Final[str] = "outbox.relay.poll_started"
LOG_PUBLISH_SUCCEEDED: Final[str] = "outbox.relay.publish_succeeded"
LOG_PUBLISH_FAILED: Final[str] = "outbox.relay.publish_failed"
LOG_EXHAUSTED: Final[str] = "outbox.relay.exhausted"
LOG_STOPPED: Final[str] = "outbox.relay.stopped"

_LOG_KEYS: Final[frozenset[str]] = frozenset(
    {
        LOG_POLL_STARTED,
        LOG_PUBLISH_SUCCEEDED,
        LOG_PUBLISH_FAILED,
        LOG_EXHAUSTED,
        LOG_STOPPED,
    }
)


class OutboxRelayWorker:
    """Per-service async relay: poll outbox_events → publish to NATS → mark.

    See module docstring for transaction & lock semantics + shutdown
    ordering contract.
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool[asyncpg.Record],
        bus: BusProtocol,
        service: str,
        settings: OutboxRelaySettings,
        bound_logger: BoundLogger,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._pool = pool
        self._bus = bus
        self._service = service
        self._settings = settings
        self._logger = bound_logger
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    async def run(self) -> None:
        """Main loop: poll → publish → mark; sleep poll_interval_s when batch empty.

        Returns cleanly on stop() via cancellation propagation. CancelledError
        from in-flight ``bus.publish`` propagates UP uncaught; the surrounding
        ``async with conn.transaction():`` rolls back any pending mark_*
        writes; rows return to pending state for next poll.
        """
        self._task = asyncio.current_task()
        try:
            while True:
                processed = await self._run_one_batch()
                if processed == 0:
                    await asyncio.sleep(self._settings.poll_interval_s)
        except asyncio.CancelledError:
            # Silent cancel per OQ-2: re-raise to terminate the task cleanly.
            raise

    async def _run_one_batch(self) -> int:
        """Single poll iteration. Returns number of events processed.

        Caller (run loop) sleeps when this returns 0 (empty batch); re-polls
        immediately when > 0. Per-event publish is SERIAL inside one batch tx.
        """
        self._logger.info(
            LOG_POLL_STARTED,
            service=self._service,
            batch_size=self._settings.batch_size,
        )
        async with self._pool.acquire() as conn, conn.transaction():
            now = self._clock()
            events = await select_pending_outbox_events(
                conn,
                service=self._service,
                batch_size=self._settings.batch_size,
                now=now,
                backoff_base_s=self._settings.backoff_base_s,
                backoff_cap_s=self._settings.backoff_cap_s,
            )
            if not events:
                return 0
            for event in events:
                # Per AC#7 + WG#4: catch Exception (NOT BaseException);
                # CancelledError propagates UP, conn.transaction() __aexit__
                # rolls back the entire batch's mark_* writes.
                try:
                    envelope = MessageEnvelope(
                        correlation_id=CorrelationId(event.correlation_id or ""),
                        publisher=self._service,
                        payload=event.payload,
                    )
                    await self._bus.publish(event.subject, envelope)
                except Exception as exc:
                    failed_now = self._clock()
                    await mark_outbox_event_failed(
                        conn,
                        event_id=event.id,
                        last_attempt_at=failed_now,
                        last_error=str(exc),
                        max_attempts=self._settings.max_attempts,
                        failed_at=failed_now,
                    )
                    self._logger.error(
                        LOG_PUBLISH_FAILED,
                        service=self._service,
                        event_id=event.id,
                        subject=event.subject,
                        correlation_id=event.correlation_id,
                        attempt_count=event.attempt_count,
                        error=str(exc),
                    )
                    if event.attempt_count + 1 >= self._settings.max_attempts:
                        self._logger.error(
                            LOG_EXHAUSTED,
                            service=self._service,
                            event_id=event.id,
                            subject=event.subject,
                            correlation_id=event.correlation_id,
                            attempt_count=event.attempt_count + 1,
                        )
                else:
                    await mark_outbox_event_published(
                        conn,
                        event_id=event.id,
                        published_at=self._clock(),
                    )
                    self._logger.info(
                        LOG_PUBLISH_SUCCEEDED,
                        service=self._service,
                        event_id=event.id,
                        subject=event.subject,
                        correlation_id=event.correlation_id,
                        attempt_count=event.attempt_count,
                    )
            return len(events)

    async def stop(self) -> None:
        """Cancel running task + await termination + emit stopped log.

        Idempotent: multiple calls produce one ``stopped`` log + same end
        state. Safe to call before run() (lifespan-startup-failure path).
        """
        if self._stopped:
            return
        self._stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._logger.info(LOG_STOPPED, service=self._service)
