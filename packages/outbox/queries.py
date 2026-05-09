"""SQL helpers for the ``outbox_events`` table (T-537a1).

Four helpers backing the outbox pattern (per BRIEF §8 / audit Items 2 + 7):

* :func:`insert_outbox_event` — ``@non_idempotent``. Caller composes inside
  the same business tx (e.g. signal-gateway's ``insert_signal`` tx in
  T-537b). State and publish-intent become atomic.
* :func:`select_pending_outbox_events` — read-side cursor with
  ``FOR UPDATE SKIP LOCKED`` + backoff-window filter computed entirely
  in SQL (single source of truth per WG#5; Python never duplicates the
  ``min(base * 2^N, cap)`` math).
* :func:`mark_outbox_event_published` — ``@idempotent``. UPDATE WHERE id=$1
  flipping ``published_at``; safe to retry (same id + same value =
  same end state).
* :func:`mark_outbox_event_failed` — ``@non_idempotent``. Increments
  ``attempt_count`` + records error; ``CASE``-driven ``failed_at`` flip
  when attempts exhausted. T-537a2 worker MUST guarantee one call per
  failed attempt.

JSONB codec-immune contract (per L-013): ``insert_outbox_event``
serialises ``payload`` via ``json.dumps(_to_jsonable(payload))`` and binds
to ``$N::jsonb``. This works regardless of whether the calling service
registered the asyncpg JSONB codec
(:func:`packages.db.queries.audit._register_jsonb_codec`). Mirror
:mod:`packages.db.queries.audit` precedent.

If a calling service ever flips its codec-registration state (signal-
gateway / execution-service / strategy-engine currently do NOT register
the JSONB codec; analytics-api / feature-engine DO register), the
correct migration is to switch to ``_to_jsonable(payload)`` pass-as-dict
form (the codec then handles the JSON encoding once). The current
double-encode-safe form is the conservative default for cross-service
shared infra.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from packages.core import idempotent, non_idempotent
from packages.db.queries.audit import _to_jsonable

from .types import OutboxEvent

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = [
    "insert_outbox_event",
    "mark_outbox_event_failed",
    "mark_outbox_event_published",
    "select_pending_outbox_events",
]


@non_idempotent
async def insert_outbox_event(
    conn: _DbExecutor,
    *,
    service: str,
    subject: str,
    correlation_id: str | None,
    payload: dict[str, Any],
    created_at: datetime,
) -> int:
    """INSERT into ``outbox_events``; returns new BIGSERIAL id.

    Caller composes inside their business tx so state-and-publish-intent
    commit atomically. Relay worker (T-537a2) eventually publishes via
    :func:`select_pending_outbox_events` + bus.publish.

    Payload uses ``json.dumps(_to_jsonable(payload))`` codec-immune form
    per L-013 — see module docstring.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO outbox_events (service, subject, correlation_id, payload, created_at)
        VALUES ($1, $2, $3, $4::jsonb, $5)
        RETURNING id
        """,
        service,
        subject,
        correlation_id,
        json.dumps(_to_jsonable(payload)),
        created_at,
    )
    if row is None:
        # RETURNING id with INSERT … VALUES (...) cannot return None unless
        # the row was filtered (e.g. by a hypothetical RLS policy). Defensive
        # raise pins the invariant for mypy + future RLS additions.
        msg = "INSERT … RETURNING id returned no row"
        raise RuntimeError(msg)
    return int(row["id"])


async def select_pending_outbox_events(
    conn: _DbExecutor,
    *,
    service: str,
    batch_size: int,
    now: datetime,
    backoff_base_s: float,
    backoff_cap_s: float,
) -> list[OutboxEvent]:
    """SELECT pending rows for ``service``, ordered by ``created_at`` ASC.

    Filter:

    .. code-block:: sql

        published_at IS NULL
        AND failed_at IS NULL
        AND (
            last_attempt_at IS NULL
            OR last_attempt_at <= $now - make_interval(secs => least(
                $backoff_base_s * power(2.0, attempt_count),
                $backoff_cap_s
            ))
        )

    Backoff math is single-source-of-truth in SQL via PG ``power`` per
    WG#5; Python never duplicates the calculation.

    ``FOR UPDATE SKIP LOCKED`` honors future horizontal scale-out: multiple
    relay replicas of the same service can run select_pending in parallel
    without duplicate publishes (each replica sees a disjoint set).

    Returns list of :class:`OutboxEvent` projections.
    """
    rows = await conn.fetch(
        """
        SELECT id, service, subject, correlation_id, payload,
               created_at, published_at, attempt_count, last_attempt_at,
               last_error, failed_at
        FROM outbox_events
        WHERE service = $1
          AND published_at IS NULL
          AND failed_at IS NULL
          AND (
              last_attempt_at IS NULL
              OR last_attempt_at <= $2::timestamptz - make_interval(
                  secs => least($3 * power(2.0, attempt_count), $4)
              )
          )
        ORDER BY created_at ASC
        LIMIT $5
        FOR UPDATE SKIP LOCKED
        """,
        service,
        now,
        backoff_base_s,
        backoff_cap_s,
        batch_size,
    )
    return [
        OutboxEvent(
            id=int(row["id"]),
            service=str(row["service"]),
            subject=str(row["subject"]),
            correlation_id=(
                str(row["correlation_id"]) if row["correlation_id"] is not None else None
            ),
            payload=_decode_jsonb(row["payload"]),
            created_at=row["created_at"],
            published_at=row["published_at"],
            attempt_count=int(row["attempt_count"]),
            last_attempt_at=row["last_attempt_at"],
            last_error=(str(row["last_error"]) if row["last_error"] is not None else None),
            failed_at=row["failed_at"],
        )
        for row in rows
    ]


@idempotent
async def mark_outbox_event_published(
    conn: _DbExecutor,
    *,
    event_id: int,
    published_at: datetime,
) -> None:
    """UPDATE ``outbox_events`` SET ``published_at`` for the row.

    Idempotent: same id + same published_at = same end state. Safe to
    retry on transient conn errors.
    """
    await conn.execute(
        """
        UPDATE outbox_events
        SET published_at = $2
        WHERE id = $1
        """,
        event_id,
        published_at,
    )


@non_idempotent
async def mark_outbox_event_failed(
    conn: _DbExecutor,
    *,
    event_id: int,
    last_attempt_at: datetime,
    last_error: str,
    max_attempts: int,
    failed_at: datetime,
) -> None:
    """Increment ``attempt_count`` + record error; flip ``failed_at`` on exhaustion.

    SQL:

    .. code-block:: sql

        UPDATE outbox_events
        SET attempt_count = attempt_count + 1,
            last_attempt_at = $2,
            last_error = $3,
            failed_at = CASE WHEN attempt_count + 1 >= $4 THEN $5::timestamptz ELSE NULL END
        WHERE id = $1

    ``@non_idempotent``: each call advances ``attempt_count``. T-537a2
    worker MUST guarantee one call per failed publish attempt.
    """
    await conn.execute(
        """
        UPDATE outbox_events
        SET attempt_count = attempt_count + 1,
            last_attempt_at = $2,
            last_error = $3,
            failed_at = CASE WHEN attempt_count + 1 >= $4 THEN $5::timestamptz ELSE NULL END
        WHERE id = $1
        """,
        event_id,
        last_attempt_at,
        last_error,
        max_attempts,
        failed_at,
    )


def _decode_jsonb(value: Any) -> dict[str, Any]:
    """Robust decode for JSONB payload from asyncpg.

    Without a registered codec, asyncpg returns ``str``; with the analytics-
    api / feature-engine codec it returns ``dict``. Handle both.
    """
    if isinstance(value, str):
        decoded: dict[str, Any] = json.loads(value)
        return decoded
    if isinstance(value, dict):
        return value
    msg = f"unexpected payload type: {type(value).__name__}"
    raise TypeError(msg)
