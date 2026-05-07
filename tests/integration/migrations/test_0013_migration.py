"""Integration test for migration 0013 (T-501 — brief §12.2:1969-1971, §7.2:983-1009).

Runs ``alembic upgrade head`` against a throwaway database and verifies
``backtest_trades`` table landed exactly as specified.

Schema lock-site per T-501 plan-doc §Schema-design (22 columns; mirror
live ``trades`` plus ``run_id`` FK):

* 22 columns in declared order with expected types + nullability.
* FK ``run_id → backtest_runs(id) ON DELETE CASCADE``.
* 3 btree indexes: ``backtest_trades_run_id`` + ``backtest_trades_run_closed``
  (partial WHERE status='closed') + ``backtest_trades_run_status``.
* NOT a hypertable (low-volume per OQ-2=A).
* ``meta`` column NOT NULL with server-default ``'{}'::jsonb``.
* Downgrade drops table + indexes cleanly (pgcrypto preserved per
  T-407 WG#6 — extension survives across migrations).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset (mirror
0012 pattern).
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

_EXPECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("run_id", "uuid", "NO"),
    ("bot_id", "text", "NO"),
    ("signal_id", "bigint", "YES"),
    ("open_order_id", "bigint", "YES"),
    ("close_order_id", "bigint", "YES"),
    ("symbol", "text", "NO"),
    ("side", "text", "NO"),
    ("entry_price", "numeric", "NO"),
    ("exit_price", "numeric", "YES"),
    ("qty", "numeric", "NO"),
    ("notional_usd", "numeric", "NO"),
    ("realized_pnl", "numeric", "YES"),
    ("fees_paid", "numeric", "YES"),
    ("close_reason", "text", "YES"),
    ("opened_at", "timestamp with time zone", "NO"),
    ("closed_at", "timestamp with time zone", "YES"),
    ("status", "text", "NO"),
    ("mfe_pct", "double precision", "YES"),
    ("mae_pct", "double precision", "YES"),
    ("confidence_score", "double precision", "YES"),
    ("meta", "jsonb", "NO"),
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


async def _insert_run(conn: asyncpg.Connection) -> _uuid_mod.UUID:
    """Helper: insert a parent backtest_runs row and return its id."""
    row = await conn.fetchrow(
        """
        INSERT INTO backtest_runs (
            name, bot_id, config_yaml, config_hash,
            date_range_start, date_range_end, status, started_at
        ) VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7)
        RETURNING id
        """,
        "fixture run",
        "alpha",
        "bot_id: alpha\n",
        "0" * 64,
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
        datetime(2026, 5, 7, 12, 0, tzinfo=UTC),
    )
    assert row is not None
    return row["id"]


async def test_migration_0013_creates_backtest_trades_table(
    migrated_db_dsn: str,
) -> None:
    """22-column shape verbatim per T-501 §Schema-design."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'backtest_trades' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_COLUMNS
    finally:
        await conn.close()


async def test_migration_0013_creates_btree_indexes(
    migrated_db_dsn: str,
) -> None:
    """3 btree indexes present (run_id + run_closed partial + run_status)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        index_names = [
            row["indexname"]
            for row in await conn.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'backtest_trades'"
            )
        ]
        assert "backtest_trades_run_id" in index_names
        assert "backtest_trades_run_closed" in index_names
        assert "backtest_trades_run_status" in index_names
    finally:
        await conn.close()


async def test_migration_0013_fk_cascade_to_backtest_runs(
    migrated_db_dsn: str,
) -> None:
    """OQ-3=A — DELETE parent backtest_runs row cascade-deletes child trades."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        run_id = await _insert_run(conn)
        await conn.execute(
            """
            INSERT INTO backtest_trades (
                run_id, bot_id, symbol, side,
                entry_price, qty, notional_usd, opened_at, status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'open')
            """,
            run_id,
            "alpha",
            "BTCUSDT",
            "buy",
            "65000",
            "0.001",
            "65.0000",
            datetime(2026, 4, 15, tzinfo=UTC),
        )
        # Pre-condition: child row exists.
        child_count_before = await conn.fetchval(
            "SELECT COUNT(*) FROM backtest_trades WHERE run_id = $1", run_id
        )
        assert child_count_before == 1

        # Cascade trigger: delete parent run.
        await conn.execute("DELETE FROM backtest_runs WHERE id = $1", run_id)

        # Post-condition: child row gone via cascade.
        child_count_after = await conn.fetchval(
            "SELECT COUNT(*) FROM backtest_trades WHERE run_id = $1", run_id
        )
        assert child_count_after == 0
    finally:
        await conn.close()


async def test_migration_0013_table_is_not_hypertable(
    migrated_db_dsn: str,
) -> None:
    """OQ-2=A — backtest_trades is low-volume; NOT promoted to hypertable."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        hypertable_row = await conn.fetchrow(
            "SELECT 1 FROM _timescaledb_catalog.hypertable WHERE table_name = 'backtest_trades'"
        )
        assert hypertable_row is None
    finally:
        await conn.close()


async def test_migration_0013_meta_default_is_empty_jsonb(
    migrated_db_dsn: str,
) -> None:
    """OQ-4=A — meta column has server-default '{}'::jsonb (mirror live trades)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        run_id = await _insert_run(conn)
        # INSERT without specifying meta — server-default applies.
        trade_id = await conn.fetchval(
            """
            INSERT INTO backtest_trades (
                run_id, bot_id, symbol, side,
                entry_price, qty, notional_usd, opened_at, status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'open')
            RETURNING id
            """,
            run_id,
            "alpha",
            "BTCUSDT",
            "buy",
            "65000",
            "0.001",
            "65.0000",
            datetime(2026, 4, 15, tzinfo=UTC),
        )
        meta_raw = await conn.fetchval("SELECT meta FROM backtest_trades WHERE id = $1", trade_id)
        # asyncpg without registered JSONB codec returns JSONB as str — explicit
        # json.loads decode (option (b) per T-501 plan §Test strategy test #5).
        assert json.loads(meta_raw) == {}
    finally:
        await conn.close()


async def test_migration_0013_downgrade_drops_table(
    base_dsn: str,
) -> None:
    """§N8 — downgrade to 0012 drops backtest_trades table + indexes; pgcrypto preserved."""
    throwaway_name = f"scalper_v2_mig0013_dn_{_uuid_mod.uuid4().hex[:8]}"

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
        await asyncio.to_thread(
            subprocess.run,
            # Explicit target 0012 per L-012 — robust against future migrations
            # changing alembic head (relative -1 would silently rollback the wrong
            # revision and the test_0013_drops_table assertion would still pass
            # while leaving 0013 effectively un-tested for downgrade).
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "downgrade", "0012"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": throwaway_dsn},
            cwd=_REPO_ROOT,
        )

        conn = await asyncpg.connect(dsn=throwaway_dsn)
        try:
            table_row = await conn.fetchrow(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'backtest_trades'"
            )
            assert table_row is None

            # backtest_runs (parent migration 0012) STILL present after -1 downgrade.
            parent_row = await conn.fetchrow(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'backtest_runs'"
            )
            assert parent_row is not None

            # pgcrypto extension preserved per T-407 WG#6 (forward-only safety).
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
