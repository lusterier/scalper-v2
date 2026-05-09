"""Transactional outbox primitives (T-537a1; BRIEF §8).

Public exports:

* :class:`OutboxEvent` — read-side row projection.
* :class:`OutboxRelaySettings` — env-sourced relay-worker config (consumed
  by T-537a2 :class:`OutboxRelayWorker`).
* :func:`insert_outbox_event` — write event-intent inside business tx.
* :func:`select_pending_outbox_events` — read-side cursor with
  ``FOR UPDATE SKIP LOCKED`` + backoff-window filter.
* :func:`mark_outbox_event_published` — flip ``published_at`` on success.
* :func:`mark_outbox_event_failed` — increment attempt + flip ``failed_at``
  on exhaustion.

T-537a2 will add :class:`OutboxRelayWorker` (the consumer that polls
+ publishes + marks). T-537b will integrate signal-gateway
(``insert_signal`` + ``insert_outbox_event`` in same tx; relay worker
in lifespan).
"""

from .queries import (
    insert_outbox_event,
    mark_outbox_event_failed,
    mark_outbox_event_published,
    select_pending_outbox_events,
)
from .types import OutboxEvent, OutboxRelaySettings

__all__ = [
    "OutboxEvent",
    "OutboxRelaySettings",
    "insert_outbox_event",
    "mark_outbox_event_failed",
    "mark_outbox_event_published",
    "select_pending_outbox_events",
]
