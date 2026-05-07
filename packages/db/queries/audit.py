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
on audit failure rolls back the audit write too (§16.8 atomicity
requirement).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from packages.core import non_idempotent

if TYPE_CHECKING:
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


def _to_jsonable(value: Any) -> Any:
    """Recursively convert UUID / datetime / Decimal in ``value`` to strings.

    Audit state dicts are sourced from dataclass row projections
    (BacktestRunRow, BotConfigRow, …) whose fields include ``UUID`` ids,
    ``TIMESTAMPTZ`` datetimes, and ``NUMERIC`` Decimals — none of which
    are JSON-native. Pre-converting here lets the caller pass the dict
    directly to asyncpg; the registered JSONB codec's
    :func:`json.dumps` encoder then handles only native types.

    Why not :func:`json.dumps` with ``default=str`` here: analytics-api
    registers a JSONB codec
    (``conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads)``);
    if we pre-serialise to a string the codec encodes that string a
    second time → the column stores a JSON string scalar (escaped
    ``"{\\"id\\":1,...}"``) instead of a JSONB object, breaking
    :class:`AuditEventRow` Pydantic round-trip in T-405's read path.

    §N1 invariant: ``str(datetime_with_tz)`` keeps explicit ``+00:00``
    offset for asyncpg-sourced TIMESTAMPTZ values. §5.3 invariant:
    ``str(Decimal)`` preserves full precision.
    """
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (UUID, datetime, Decimal)):
        return str(value)
    return value


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

    Column order + types match migration 0011 (§7.2:1110-1122).
    ``before_state`` / ``after_state`` dicts are pre-converted via
    :func:`_to_jsonable` (UUID/datetime/Decimal → str) and passed
    directly to asyncpg — the registered JSONB codec handles
    serialisation once. See :func:`_to_jsonable` docstring for the
    double-encoding pitfall this avoids.

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
        _to_jsonable(before_state) if before_state is not None else None,
        _to_jsonable(after_state) if after_state is not None else None,
        correlation_id,
    )
    if row is None:
        msg = "INSERT ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])
