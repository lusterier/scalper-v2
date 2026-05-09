"""Integration test for migration 0016 (T-537a1 / outbox).

Runs ``alembic upgrade head`` against a throwaway database and verifies
migration 0016 deltas:

* ``outbox_events`` table created with all 11 columns + correct types +
  nullability + defaults.
* Two partial indexes created with correct WHERE clauses
  (``outbox_events_pending_idx`` + ``outbox_events_correlation_idx``).
* INSERT + SELECT round-trip on each column type smoke-test.
* Explicit ``downgrade 0015`` target per L-012 (NEVER relative ``-1`` —
  robust against future migrations changing alembic head when 0017+ lands).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid as _uuid_mod
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


async def test_migration_0016_creates_outbox_events_table(
    migrated_db_dsn: str,
) -> None:
    """outbox_events table exists post-upgrade with correct structure."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'outbox_events' "
            "ORDER BY ordinal_position"
        )
        cols = {row["column_name"]: row for row in rows}
        # All 11 columns present.
        expected = {
            "id",
            "service",
            "subject",
            "correlation_id",
            "payload",
            "created_at",
            "published_at",
            "attempt_count",
            "last_attempt_at",
            "last_error",
            "failed_at",
        }
        assert set(cols) == expected, f"unexpected columns: {set(cols) ^ expected}"

        # Type assertions.
        assert cols["id"]["data_type"] == "bigint"
        assert cols["service"]["data_type"] == "text"
        assert cols["subject"]["data_type"] == "text"
        assert cols["correlation_id"]["data_type"] == "text"
        assert cols["payload"]["data_type"] == "jsonb"
        assert cols["created_at"]["data_type"] == "timestamp with time zone"
        assert cols["published_at"]["data_type"] == "timestamp with time zone"
        assert cols["attempt_count"]["data_type"] == "integer"
        assert cols["last_attempt_at"]["data_type"] == "timestamp with time zone"
        assert cols["last_error"]["data_type"] == "text"
        assert cols["failed_at"]["data_type"] == "timestamp with time zone"

        # Nullability.
        assert cols["id"]["is_nullable"] == "NO"
        assert cols["service"]["is_nullable"] == "NO"
        assert cols["subject"]["is_nullable"] == "NO"
        assert cols["correlation_id"]["is_nullable"] == "YES"
        assert cols["payload"]["is_nullable"] == "NO"
        assert cols["created_at"]["is_nullable"] == "NO"
        assert cols["published_at"]["is_nullable"] == "YES"
        assert cols["attempt_count"]["is_nullable"] == "NO"
        assert cols["last_attempt_at"]["is_nullable"] == "YES"
        assert cols["last_error"]["is_nullable"] == "YES"
        assert cols["failed_at"]["is_nullable"] == "YES"

        # attempt_count default = 0.
        assert cols["attempt_count"]["column_default"] == "0"
    finally:
        await conn.close()


async def test_migration_0016_creates_partial_indexes(
    migrated_db_dsn: str,
) -> None:
    """Both partial indexes exist with correct WHERE clauses."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        rows = await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename = 'outbox_events' "
            "ORDER BY indexname"
        )
        indexes = {row["indexname"]: row["indexdef"] for row in rows}
        assert "outbox_events_pending_idx" in indexes
        assert "outbox_events_correlation_idx" in indexes

        pending_def = indexes["outbox_events_pending_idx"]
        assert "(service, created_at)" in pending_def
        assert "WHERE" in pending_def
        assert "published_at IS NULL" in pending_def
        assert "failed_at IS NULL" in pending_def

        corr_def = indexes["outbox_events_correlation_idx"]
        assert "(correlation_id)" in corr_def
        assert "WHERE" in corr_def
        assert "correlation_id IS NOT NULL" in corr_def
    finally:
        await conn.close()


async def test_migration_0016_insert_and_select_round_trip(
    migrated_db_dsn: str,
) -> None:
    """Smoke-test INSERT with all column types + SELECT round-trip."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        now = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)
        payload = {"correlation_id": "cid-1", "subject": "signals.validated"}
        new_id = await conn.fetchval(
            "INSERT INTO outbox_events "
            "(service, subject, correlation_id, payload, created_at) "
            "VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id",
            "signal_gateway",
            "signals.validated",
            "cid-1",
            json.dumps(payload),
            now,
        )
        assert isinstance(new_id, int)
        assert new_id > 0

        row = await conn.fetchrow(
            "SELECT * FROM outbox_events WHERE id = $1",
            new_id,
        )
        assert row is not None
        assert row["service"] == "signal_gateway"
        assert row["subject"] == "signals.validated"
        assert row["correlation_id"] == "cid-1"
        # asyncpg returns JSONB as str when no codec registered.
        decoded = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        assert decoded == payload
        assert row["created_at"] == now
        assert row["published_at"] is None
        assert row["attempt_count"] == 0
        assert row["last_attempt_at"] is None
        assert row["last_error"] is None
        assert row["failed_at"] is None
    finally:
        await conn.close()


async def test_migration_0016_downgrade_drops_table_and_indexes(
    base_dsn: str,
) -> None:
    """§N8 + L-012 — explicit downgrade 0015 target drops table + indexes."""
    throwaway_name = f"scalper_v2_mig0016_dn_{_uuid_mod.uuid4().hex[:8]}"

    admin_conn = await asyncpg.connect(dsn=base_dsn)
    try:
        await admin_conn.execute(f'CREATE DATABASE "{throwaway_name}"')
    finally:
        await admin_conn.close()

    throwaway_dsn = _swap_database_in_dsn(base_dsn, throwaway_name)

    try:
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": throwaway_dsn},
            cwd=_REPO_ROOT,
        )
        # Explicit target 0015 per L-012 — robust against future migrations
        # changing alembic head once 0017+ lands.
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "downgrade", "0015"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": throwaway_dsn},
            cwd=_REPO_ROOT,
        )

        conn = await asyncpg.connect(dsn=throwaway_dsn)
        try:
            table_row = await conn.fetchrow(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'outbox_events'"
            )
            assert table_row is None, "downgrade should drop outbox_events table"

            idx_rows = await conn.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "  AND indexname IN ('outbox_events_pending_idx', 'outbox_events_correlation_idx')"
            )
            assert len(idx_rows) == 0, "downgrade should drop both partial indexes"
        finally:
            await conn.close()
    finally:
        admin_conn = await asyncpg.connect(dsn=base_dsn)
        try:
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{throwaway_name}" WITH (FORCE)')
        finally:
            await admin_conn.close()
