"""§N4 unit tests for :mod:`packages.outbox.queries` (T-537a1).

Mock-based: ``conn.execute`` / ``conn.fetch`` / ``conn.fetchrow`` patched.
SQL string + bind ordering verified at mock level. Round-trip + ``FOR
UPDATE SKIP LOCKED`` semantics + backoff window math verified at the
testcontainer-gated layer in ``tests/integration/queries/test_outbox.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from packages.core import is_idempotent, is_non_idempotent
from packages.outbox.queries import (
    insert_outbox_event,
    mark_outbox_event_failed,
    mark_outbox_event_published,
    select_pending_outbox_events,
)

_FIXED_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# §N3 idempotency markers
# ---------------------------------------------------------------------------


def test_insert_outbox_event_marked_non_idempotent() -> None:
    assert is_non_idempotent(insert_outbox_event)


def test_mark_outbox_event_published_marked_idempotent() -> None:
    assert is_idempotent(mark_outbox_event_published)


def test_mark_outbox_event_failed_marked_non_idempotent() -> None:
    assert is_non_idempotent(mark_outbox_event_failed)


# ---------------------------------------------------------------------------
# insert_outbox_event
# ---------------------------------------------------------------------------


async def test_insert_outbox_event_returns_new_id() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 42})
    new_id = await insert_outbox_event(
        conn,
        service="signal_gateway",
        subject="signals.validated",
        correlation_id="cid-1",
        payload={"action": "buy", "symbol": "BTCUSDT"},
        created_at=_FIXED_NOW,
    )
    assert new_id == 42


async def test_insert_outbox_event_sql_shape_and_bind_order() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    await insert_outbox_event(
        conn,
        service="signal_gateway",
        subject="signals.validated",
        correlation_id="cid-1",
        payload={"k": "v"},
        created_at=_FIXED_NOW,
    )
    args = conn.fetchrow.await_args.args
    sql = args[0]
    assert "INSERT INTO outbox_events" in sql
    assert "(service, subject, correlation_id, payload, created_at)" in sql
    assert "$4::jsonb" in sql
    assert "RETURNING id" in sql
    assert args[1] == "signal_gateway"
    assert args[2] == "signals.validated"
    assert args[3] == "cid-1"
    # payload is json.dumps(_to_jsonable(payload)) — string serialization.
    assert isinstance(args[4], str)
    assert '"k"' in args[4]
    assert '"v"' in args[4]
    assert args[5] == _FIXED_NOW


async def test_insert_outbox_event_codec_immune_uses_to_jsonable() -> None:
    """L-013 / WG#10 — payload with non-JSON-native values is pre-converted."""
    from decimal import Decimal
    from uuid import UUID

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    payload_with_decimals = {
        "qty": Decimal("0.001"),
        "id": UUID("12345678-1234-5678-1234-567812345678"),
        "ts": datetime(2026, 1, 1, tzinfo=UTC),
    }
    await insert_outbox_event(
        conn,
        service="signal_gateway",
        subject="signals.validated",
        correlation_id=None,
        payload=payload_with_decimals,
        created_at=_FIXED_NOW,
    )
    args = conn.fetchrow.await_args.args
    serialized = args[4]
    # Decimal → str via _to_jsonable; would crash json.dumps without it.
    assert "0.001" in serialized
    assert "12345678" in serialized


async def test_insert_outbox_event_correlation_id_can_be_none() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    await insert_outbox_event(
        conn,
        service="signal_gateway",
        subject="signals.validated",
        correlation_id=None,
        payload={},
        created_at=_FIXED_NOW,
    )
    args = conn.fetchrow.await_args.args
    assert args[3] is None


# ---------------------------------------------------------------------------
# select_pending_outbox_events
# ---------------------------------------------------------------------------


async def test_select_pending_outbox_events_sql_shape() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    await select_pending_outbox_events(
        conn,
        service="signal_gateway",
        batch_size=100,
        now=_FIXED_NOW,
        backoff_base_s=2.0,
        backoff_cap_s=60.0,
    )
    sql = conn.fetch.await_args.args[0]
    assert "SELECT" in sql
    assert "FROM outbox_events" in sql
    assert "WHERE service = $1" in sql
    assert "published_at IS NULL" in sql
    assert "failed_at IS NULL" in sql
    assert "make_interval" in sql
    assert "least($3 * power(2.0, attempt_count), $4)" in sql
    assert "ORDER BY created_at ASC" in sql
    assert "LIMIT $5" in sql
    assert "FOR UPDATE SKIP LOCKED" in sql


async def test_select_pending_outbox_events_returns_projection() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 1,
                "service": "signal_gateway",
                "subject": "signals.validated",
                "correlation_id": "cid-1",
                "payload": '{"k": "v"}',  # str (no codec)
                "created_at": _FIXED_NOW,
                "published_at": None,
                "attempt_count": 0,
                "last_attempt_at": None,
                "last_error": None,
                "failed_at": None,
            }
        ]
    )
    events = await select_pending_outbox_events(
        conn,
        service="signal_gateway",
        batch_size=100,
        now=_FIXED_NOW,
        backoff_base_s=2.0,
        backoff_cap_s=60.0,
    )
    assert len(events) == 1
    e = events[0]
    assert e.id == 1
    assert e.service == "signal_gateway"
    assert e.payload == {"k": "v"}
    assert e.attempt_count == 0


async def test_select_pending_outbox_events_handles_dict_payload() -> None:
    """asyncpg with codec registered returns dict directly."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": 1,
                "service": "signal_gateway",
                "subject": "signals.validated",
                "correlation_id": None,
                "payload": {"k": "v"},  # dict (codec registered)
                "created_at": _FIXED_NOW,
                "published_at": None,
                "attempt_count": 0,
                "last_attempt_at": None,
                "last_error": None,
                "failed_at": None,
            }
        ]
    )
    events = await select_pending_outbox_events(
        conn,
        service="signal_gateway",
        batch_size=100,
        now=_FIXED_NOW,
        backoff_base_s=2.0,
        backoff_cap_s=60.0,
    )
    assert events[0].payload == {"k": "v"}


# ---------------------------------------------------------------------------
# mark_outbox_event_published
# ---------------------------------------------------------------------------


async def test_mark_outbox_event_published_sql_pk_only() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    await mark_outbox_event_published(conn, event_id=42, published_at=_FIXED_NOW)
    args = conn.execute.await_args.args
    sql = args[0]
    assert "UPDATE outbox_events" in sql
    assert "SET published_at = $2" in sql
    assert "WHERE id = $1" in sql
    assert args[1] == 42
    assert args[2] == _FIXED_NOW


# ---------------------------------------------------------------------------
# mark_outbox_event_failed
# ---------------------------------------------------------------------------


async def test_mark_outbox_event_failed_sql_increments_and_case_flips() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    await mark_outbox_event_failed(
        conn,
        event_id=42,
        last_attempt_at=_FIXED_NOW,
        last_error="nats unreachable",
        max_attempts=100,
        failed_at=_FIXED_NOW,
    )
    args = conn.execute.await_args.args
    sql = args[0]
    assert "UPDATE outbox_events" in sql
    assert "attempt_count = attempt_count + 1" in sql
    assert "last_attempt_at = $2" in sql
    assert "last_error = $3" in sql
    assert "CASE WHEN attempt_count + 1 >= $4" in sql
    assert "WHERE id = $1" in sql
    assert args[1] == 42
    assert args[2] == _FIXED_NOW
    assert args[3] == "nats unreachable"
    assert args[4] == 100
    assert args[5] == _FIXED_NOW
