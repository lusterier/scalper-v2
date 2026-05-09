"""Integration tests for :mod:`packages.outbox.queries` (T-537a1).

Runs against a throwaway PostgreSQL migrated to head (includes
0016_outbox_events). Per L-008 active control: helpers exercising
non-trivial SQL (``FOR UPDATE SKIP LOCKED``, ``make_interval``,
``power``, ``CASE``-conditional flip) need a real-PG round-trip
because mock-only tests can't catch off-by-one ``$N`` bind ordering
or PG type-coercion failures.

Skipped at collection when ``POSTGRES_TEST_DSN`` is unset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from packages.outbox.queries import (
    insert_outbox_event,
    mark_outbox_event_failed,
    mark_outbox_event_published,
    select_pending_outbox_events,
)

_T_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_insert_and_select_pending_round_trip(migrated_db_dsn: str) -> None:
    """Insert event → select_pending returns it with correct projection."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        new_id = await insert_outbox_event(
            conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id="cid-1",
            payload={"action": "buy", "symbol": "BTCUSDT"},
            created_at=_T_NOW,
        )
        assert new_id > 0

        events = await select_pending_outbox_events(
            conn,
            service="signal_gateway",
            batch_size=100,
            now=_T_NOW + timedelta(seconds=10),
            backoff_base_s=2.0,
            backoff_cap_s=60.0,
        )
        assert len(events) == 1
        e = events[0]
        assert e.id == new_id
        assert e.service == "signal_gateway"
        assert e.subject == "signals.validated"
        assert e.correlation_id == "cid-1"
        assert e.payload == {"action": "buy", "symbol": "BTCUSDT"}
        assert e.attempt_count == 0
        assert e.published_at is None
        assert e.failed_at is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_mark_published_excludes_from_select_pending(migrated_db_dsn: str) -> None:
    """After mark_published, select_pending returns empty for that row."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        new_id = await insert_outbox_event(
            conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id=None,
            payload={"k": "v"},
            created_at=_T_NOW,
        )
        await mark_outbox_event_published(
            conn,
            event_id=new_id,
            published_at=_T_NOW + timedelta(seconds=1),
        )
        events = await select_pending_outbox_events(
            conn,
            service="signal_gateway",
            batch_size=100,
            now=_T_NOW + timedelta(seconds=10),
            backoff_base_s=2.0,
            backoff_cap_s=60.0,
        )
        assert events == []

        # Row still exists with published_at set.
        row = await conn.fetchrow("SELECT published_at FROM outbox_events WHERE id = $1", new_id)
        assert row is not None
        assert row["published_at"] == _T_NOW + timedelta(seconds=1)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_mark_failed_increments_attempt_count_no_failed_at_below_max(
    migrated_db_dsn: str,
) -> None:
    """mark_failed when attempt_count + 1 < max_attempts → failed_at stays NULL."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        new_id = await insert_outbox_event(
            conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id=None,
            payload={"k": "v"},
            created_at=_T_NOW,
        )
        await mark_outbox_event_failed(
            conn,
            event_id=new_id,
            last_attempt_at=_T_NOW + timedelta(seconds=1),
            last_error="nats unreachable",
            max_attempts=100,
            failed_at=_T_NOW + timedelta(seconds=1),
        )
        row = await conn.fetchrow(
            "SELECT attempt_count, last_attempt_at, last_error, failed_at "
            "FROM outbox_events WHERE id = $1",
            new_id,
        )
        assert row is not None
        assert row["attempt_count"] == 1
        assert row["last_attempt_at"] == _T_NOW + timedelta(seconds=1)
        assert row["last_error"] == "nats unreachable"
        assert row["failed_at"] is None  # 1 < 100, not exhausted
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_mark_failed_sets_failed_at_when_attempts_exhausted(
    migrated_db_dsn: str,
) -> None:
    """mark_failed when attempt_count + 1 >= max_attempts → failed_at flipped."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        new_id = await insert_outbox_event(
            conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id=None,
            payload={"k": "v"},
            created_at=_T_NOW,
        )
        # Max=2 → first call attempt_count goes 0→1 (not yet >= 2), no failed_at.
        await mark_outbox_event_failed(
            conn,
            event_id=new_id,
            last_attempt_at=_T_NOW + timedelta(seconds=1),
            last_error="err1",
            max_attempts=2,
            failed_at=_T_NOW + timedelta(seconds=10),
        )
        row1 = await conn.fetchrow(
            "SELECT attempt_count, failed_at FROM outbox_events WHERE id = $1", new_id
        )
        assert row1 is not None
        assert row1["attempt_count"] == 1
        assert row1["failed_at"] is None

        # Second call: attempt_count goes 1→2, 2 >= 2 → failed_at flipped.
        await mark_outbox_event_failed(
            conn,
            event_id=new_id,
            last_attempt_at=_T_NOW + timedelta(seconds=2),
            last_error="err2",
            max_attempts=2,
            failed_at=_T_NOW + timedelta(seconds=2),
        )
        row2 = await conn.fetchrow(
            "SELECT attempt_count, failed_at, last_error FROM outbox_events WHERE id = $1",
            new_id,
        )
        assert row2 is not None
        assert row2["attempt_count"] == 2
        assert row2["failed_at"] == _T_NOW + timedelta(seconds=2)
        assert row2["last_error"] == "err2"

        # select_pending now excludes the failed row.
        events = await select_pending_outbox_events(
            conn,
            service="signal_gateway",
            batch_size=100,
            now=_T_NOW + timedelta(minutes=10),
            backoff_base_s=2.0,
            backoff_cap_s=60.0,
        )
        assert events == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_select_pending_backoff_window_excludes_recent_attempts(
    migrated_db_dsn: str,
) -> None:
    """T-537a1 WG#5 / WG#13 — backoff window in SQL via PG ``power``.

    Hardcoded expected window per WG#5 (NOT Python-side recompute):
    base=2.0, attempt_count=1 → least(2.0 * power(2.0, 1), 60.0) = 4.0 seconds.
    Row with last_attempt_at = now - 0.5s is NOT returned (still in backoff).
    Row with last_attempt_at = now - 5s IS returned (window expired).
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # Insert two rows — both bumped to attempt_count=1; one inside the 4s
        # window, one outside.
        new_id_recent = await insert_outbox_event(
            conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id=None,
            payload={"slot": "recent"},
            created_at=_T_NOW,
        )
        new_id_stale = await insert_outbox_event(
            conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id=None,
            payload={"slot": "stale"},
            created_at=_T_NOW,
        )

        # Bump both to attempt_count=1 with distinct last_attempt_at via direct UPDATE
        # (mark_failed would advance to 2; here we just want to set the timestamp).
        await conn.execute(
            "UPDATE outbox_events SET attempt_count = 1, last_attempt_at = $1 WHERE id = $2",
            _T_NOW - timedelta(seconds=0, milliseconds=500),  # 0.5s ago
            new_id_recent,
        )
        await conn.execute(
            "UPDATE outbox_events SET attempt_count = 1, last_attempt_at = $1 WHERE id = $2",
            _T_NOW - timedelta(seconds=5),  # 5s ago
            new_id_stale,
        )

        # Backoff window: base=2.0, attempt_count=1 → 2.0 * 2.0 = 4.0 seconds.
        # Recent (0.5s ago) is INSIDE the 4s window → excluded.
        # Stale (5s ago) is OUTSIDE → included.
        events = await select_pending_outbox_events(
            conn,
            service="signal_gateway",
            batch_size=100,
            now=_T_NOW,
            backoff_base_s=2.0,
            backoff_cap_s=60.0,
        )
        ids_returned = [e.id for e in events]
        assert new_id_recent not in ids_returned, (
            "recent (0.5s ago, attempt=1) should be in 4s backoff window — excluded"
        )
        assert new_id_stale in ids_returned, (
            "stale (5s ago, attempt=1) should be past the 4s window — included"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_select_pending_filters_by_service_discriminator(
    migrated_db_dsn: str,
) -> None:
    """Only rows matching ``service`` filter are returned."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        sg_id = await insert_outbox_event(
            conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id=None,
            payload={"k": "sg"},
            created_at=_T_NOW,
        )
        await insert_outbox_event(
            conn,
            service="execution",
            subject="orders.events",
            correlation_id=None,
            payload={"k": "exec"},
            created_at=_T_NOW,
        )
        events = await select_pending_outbox_events(
            conn,
            service="signal_gateway",
            batch_size=100,
            now=_T_NOW + timedelta(seconds=10),
            backoff_base_s=2.0,
            backoff_cap_s=60.0,
        )
        assert len(events) == 1
        assert events[0].id == sg_id
        assert events[0].service == "signal_gateway"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_select_pending_for_update_skip_locked_disjoint_replicas(
    base_dsn: str, migrated_db_dsn: str
) -> None:
    """FOR UPDATE SKIP LOCKED — two concurrent transactions return disjoint sets.

    Insert 2 rows. Open two transactions in parallel; each calls
    select_pending. The first tx locks both rows; the second tx must see
    SKIP LOCKED and return empty (or vice versa). Aggregate of the two
    results must be exactly the 2 rows (no double-publish risk).
    """
    seed_conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await insert_outbox_event(
            seed_conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id=None,
            payload={"slot": "a"},
            created_at=_T_NOW,
        )
        await insert_outbox_event(
            seed_conn,
            service="signal_gateway",
            subject="signals.validated",
            correlation_id=None,
            payload={"slot": "b"},
            created_at=_T_NOW,
        )
    finally:
        await seed_conn.close()

    # Two parallel connections.
    conn_a = await asyncpg.connect(dsn=migrated_db_dsn)
    conn_b = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        tx_a = conn_a.transaction()
        tx_b = conn_b.transaction()
        await tx_a.start()
        await tx_b.start()

        # Race: A grabs the lock first; B sees SKIP LOCKED.
        events_a = await select_pending_outbox_events(
            conn_a,
            service="signal_gateway",
            batch_size=100,
            now=_T_NOW + timedelta(seconds=10),
            backoff_base_s=2.0,
            backoff_cap_s=60.0,
        )
        events_b = await select_pending_outbox_events(
            conn_b,
            service="signal_gateway",
            batch_size=100,
            now=_T_NOW + timedelta(seconds=10),
            backoff_base_s=2.0,
            backoff_cap_s=60.0,
        )

        # Disjoint: rows seen by A must NOT appear in B's set.
        ids_a = {e.id for e in events_a}
        ids_b = {e.id for e in events_b}
        assert ids_a.isdisjoint(ids_b), f"FOR UPDATE SKIP LOCKED violation: A={ids_a}, B={ids_b}"
        assert len(ids_a) + len(ids_b) == 2, (
            "aggregate must equal 2 inserted rows; SKIP LOCKED must not "
            "permanently hide rows beyond the lock holder's tx"
        )

        await tx_a.rollback()
        await tx_b.rollback()
    finally:
        await conn_a.close()
        await conn_b.close()
    # Suppress unused-arg lint for base_dsn (declared for fixture symmetry with sibling tests).
    _ = base_dsn
