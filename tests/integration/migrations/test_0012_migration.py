"""Integration test for migration 0012 (T-407 — brief §7.2:1141-1156, §9.6:1629).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the ``backtest_runs`` table + ``pgcrypto`` extension landed exactly as
specified.

Schema lock-site per §7.2:1144-1156 verbatim:

* 11 brief-spec columns + 12th ``bot_id`` (T-407 plan addition for
  T-415 per-bot historic-runs UI filter).
* ``id UUID PRIMARY KEY DEFAULT gen_random_uuid()`` — requires
  pgcrypto extension (BLOCKER #2 fix; FIRST migration in repo to need it).
* NOT a hypertable (low-volume per OQ-1=A).
* 2 btree indexes: ``backtest_runs_started_at_desc`` +
  ``backtest_runs_bot_id_started``.
* Downgrade preserves pgcrypto extension (WG#6 — N8 forward-only safety).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset (mirror
0011 pattern).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid as _uuid_mod
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg

_EXPECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "uuid", "NO"),
    ("name", "text", "NO"),
    ("bot_id", "text", "NO"),
    ("config_yaml", "text", "NO"),
    ("config_hash", "text", "NO"),
    ("date_range_start", "timestamp with time zone", "NO"),
    ("date_range_end", "timestamp with time zone", "NO"),
    ("status", "text", "NO"),
    ("started_at", "timestamp with time zone", "NO"),
    ("finished_at", "timestamp with time zone", "YES"),
    ("summary", "jsonb", "YES"),
    ("notes", "text", "YES"),
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


async def test_migration_0012_creates_pgcrypto_extension(
    migrated_db_dsn: str,
) -> None:
    """WG#5 — pgcrypto present after upgrade (FIRST migration in repo to add it)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # pg_extension catalog query — extension must be installed.
        ext_row = await conn.fetchrow("SELECT extname FROM pg_extension WHERE extname = 'pgcrypto'")
        assert ext_row is not None
        assert ext_row["extname"] == "pgcrypto"
    finally:
        await conn.close()


async def test_migration_0012_creates_backtest_runs_table(
    migrated_db_dsn: str,
) -> None:
    """12-column shape verbatim per §7.2:1144-1156 + 12th bot_id."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'backtest_runs' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_COLUMNS
    finally:
        await conn.close()


async def test_migration_0012_creates_btree_indexes(
    migrated_db_dsn: str,
) -> None:
    """Both btree indexes exist (started_at_desc + bot_id_started)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        index_names = [
            row["indexname"]
            for row in await conn.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'backtest_runs'"
            )
        ]
        assert "backtest_runs_started_at_desc" in index_names
        assert "backtest_runs_bot_id_started" in index_names
    finally:
        await conn.close()


async def test_migration_0012_table_is_not_hypertable(
    migrated_db_dsn: str,
) -> None:
    """OQ-1=A — backtest_runs is low-volume; NOT promoted to hypertable."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        hypertable_row = await conn.fetchrow(
            "SELECT 1 FROM _timescaledb_catalog.hypertable WHERE table_name = 'backtest_runs'"
        )
        assert hypertable_row is None
    finally:
        await conn.close()


async def test_migration_0012_uuid_default_is_gen_random_uuid(
    migrated_db_dsn: str,
) -> None:
    """`id` UUID column server default = gen_random_uuid() (pgcrypto-backed)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        default_expr = await conn.fetchval(
            """
            SELECT pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attrdef d
            JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE a.attrelid = 'public.backtest_runs'::regclass
              AND a.attname = 'id'
            """
        )
        assert default_expr == "gen_random_uuid()"

        # Functional check: INSERT without specifying id auto-fills via gen_random_uuid().
        from datetime import UTC, datetime

        await conn.execute(
            """
            INSERT INTO backtest_runs (
                name, bot_id, config_yaml, config_hash,
                date_range_start, date_range_end, status, started_at
            ) VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7)
            """,
            "smoke test run",
            "alpha",
            "bot_id: alpha\n",
            "deadbeef" * 8,
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 5, 1, tzinfo=UTC),
            datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
        )
        row = await conn.fetchrow("SELECT id FROM backtest_runs WHERE name = $1", "smoke test run")
        assert row is not None
        # id should be a valid UUID populated by gen_random_uuid().
        assert isinstance(row["id"], _uuid_mod.UUID)
    finally:
        await conn.close()


async def test_migration_0012_downgrade_preserves_pgcrypto(
    base_dsn: str,
) -> None:
    """WG#6 — downgrade drops table+indexes; pgcrypto extension preserved (N8 forward-only)."""
    throwaway_name = f"scalper_v2_mig0012_dn_{_uuid_mod.uuid4().hex[:8]}"

    admin_conn = await asyncpg.connect(dsn=base_dsn)
    try:
        await admin_conn.execute(f'CREATE DATABASE "{throwaway_name}"')
    finally:
        await admin_conn.close()

    throwaway_dsn = _swap_database_in_dsn(base_dsn, throwaway_name)

    try:
        # upgrade head
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": throwaway_dsn},
            cwd=_REPO_ROOT,
        )

        # downgrade -1 (rollback 0012 only)
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "downgrade", "-1"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": throwaway_dsn},
            cwd=_REPO_ROOT,
        )

        conn = await asyncpg.connect(dsn=throwaway_dsn)
        try:
            # Table dropped.
            table_row = await conn.fetchrow(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'backtest_runs'"
            )
            assert table_row is None

            # pgcrypto extension PRESERVED per WG#6.
            ext_row = await conn.fetchrow(
                "SELECT extname FROM pg_extension WHERE extname = 'pgcrypto'"
            )
            assert ext_row is not None
        finally:
            await conn.close()
    finally:
        admin_conn = await asyncpg.connect(dsn=base_dsn)
        try:
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{throwaway_name}" WITH (FORCE)')
        finally:
            await admin_conn.close()
