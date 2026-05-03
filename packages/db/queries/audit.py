"""audit-events query module (§7.2:1108-1126, §16.8:2261-2264).

Owned by ``packages/db/queries``; consumed by analytics-api admin write
endpoints to record one row per mutation per §16.8:2261 ("Write
endpoints log every action to ``audit_events`` with ``actor``,
``before_state``, ``after_state``").

T-401b symbol-map CRUD is the first consumer. Helper ships standalone
in T-401a per L-007 pre-emptive split — alternative (merging
T-401a+T-401b) would overshoot §0.3 LOC cap (~505 LOC vs 400). T-405
audit-log viewer becomes the first reader of the rows this helper
inserts.

The helper writes inside the caller's ``conn.transaction()`` so the
audit row commits atomically with the business mutation; tx rollback
on audit failure rolls back the business write too (§16.8 atomicity
requirement).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.core import non_idempotent

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = ["AuditEventRow", "insert_audit_event"]


@dataclass(frozen=True, slots=True)
class AuditEventRow:
    """Read projection of ``audit_events`` row (consumed by T-405 viewer)."""

    id: int
    occurred_at: datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    correlation_id: str | None
    meta: dict[str, Any]


@non_idempotent
async def insert_audit_event(
    conn: _DbExecutor,
    *,
    occurred_at: datetime,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
    correlation_id: str | None,
) -> int:
    """Insert one row into ``audit_events`` and return the generated ``id``.

    Marked ``@non_idempotent`` per §N3 / §5.8: callers do **not** retry
    on failure — caller is expected to invoke this INSIDE the same
    ``conn.transaction()`` as the business mutation; any failure rolls
    back the mutation too.

    Column order + types match migration 0011 (§7.2:1110-1122). The
    ``before_state`` / ``after_state`` dicts are serialised via
    :func:`json.dumps` and cast to ``jsonb`` server-side; the default
    asyncpg codec map does not auto-convert Python dicts to ``jsonb``.
    Same pattern as :func:`packages.db.queries.signal_gateway.insert_signal`.

    ``before_state`` is ``None`` for create actions; ``after_state`` is
    ``None`` for delete actions; both non-``None`` for update actions.
    Both ``None`` is allowed at the DB layer but semantically odd —
    callers should set at least one.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO audit_events (
            occurred_at, actor, action, entity_type, entity_id,
            before_state, after_state, correlation_id
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
        RETURNING id
        """,
        occurred_at,
        actor,
        action,
        entity_type,
        entity_id,
        json.dumps(before_state) if before_state is not None else None,
        json.dumps(after_state) if after_state is not None else None,
        correlation_id,
    )
    if row is None:
        msg = "INSERT ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])
