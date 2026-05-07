"""Integration test for migration 0014 (T-510a — brief §13.3 + §13.5).

Runs ``alembic upgrade head`` against a throwaway database and verifies
both ``shadow_variants`` and ``shadow_rejected`` tables landed exactly
as specified.

Schema lock-site per T-510a plan-doc §Schema-design:

* ``shadow_variants`` — 14 columns; FK ``parent_trade_id → trades(id)
  ON DELETE CASCADE``.
* ``shadow_rejected`` — 11 columns; **NO FK on signal_id** per OQ-6=A
  (signals hypertable composite PK rejects FK on id alone; mirror
  0005/0008/0010/0013 sibling convention).
* 4 btree indexes total: ``shadow_variants_parent`` +
  ``shadow_variants_bot_active`` (partial WHERE terminated_at IS NULL)
  + ``shadow_rejected_signal`` + ``shadow_rejected_bot_active``
  (partial WHERE terminated_at IS NULL).
* Both tables NOT hypertable per OQ-2=A (low volume; mirror T-501).
* ``meta JSONB NOT NULL DEFAULT '{}'::jsonb`` on both tables.
* Downgrade drops both tables + indexes; **explicit `downgrade 0013`
  target per L-012 lesson** (NEVER relative `-1` — robust against
  future migrations changing alembic head).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset (mirror
0013 pattern).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid as _uuid_mod
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg

_EXPECTED_VARIANTS_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("parent_trade_id", "bigint", "NO"),
    ("bot_id", "text", "NO"),
    ("variant_name", "text", "NO"),
    ("side", "text", "NO"),
    ("entry_price", "numeric", "NO"),
    ("qty", "numeric", "NO"),
    ("created_at", "timestamp with time zone", "NO"),
    ("terminated_at", "timestamp with time zone", "YES"),
    ("terminal_outcome", "text", "YES"),
    ("realized_pnl", "numeric", "YES"),
    ("mfe_pct", "double precision", "YES"),
    ("mae_pct", "double precision", "YES"),
    ("meta", "jsonb", "NO"),
)

_EXPECTED_REJECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("signal_id", "bigint", "NO"),
    ("bot_id", "text", "NO"),
    ("symbol", "text", "NO"),
    ("would_side", "text", "NO"),
    ("created_at", "timestamp with time zone", "NO"),
    ("terminated_at", "timestamp with time zone", "YES"),
    ("terminal_outcome", "text", "YES"),
    ("mfe_pct", "double precision", "YES"),
    ("mae_pct", "double precision", "YES"),
    ("meta", "jsonb", "NO"),
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


async def _insert_trade(conn: asyncpg.Connection) -> int:
    """Helper: insert bots + orders + trades chain; return parent trade_id."""
    bot_id = f"t510a_{_uuid_mod.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO bots "
        "(bot_id, display_name, created_at, status, exchange_mode, "
        " config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        bot_id,
        "T-510a fixture",
        datetime(2026, 5, 7, tzinfo=UTC),
        "active",
        "paper",
        "sha256:t510a",
        datetime(2026, 5, 7, tzinfo=UTC),
    )
    order_id = await conn.fetchval(
        "INSERT INTO orders "
        "(bot_id, correlation_id, exchange, symbol, side, order_type, "
        " qty, price, status, requested_at, idempotent) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
        "RETURNING id",
        bot_id,
        "corr-t510a",
        "bybit",
        "BTCUSDT",
        "buy",
        "market",
        Decimal("0.001"),
        Decimal("65000"),
        "filled",
        datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
        False,
    )
    trade_id = await conn.fetchval(
        "INSERT INTO trades "
        "(bot_id, open_order_id, symbol, side, entry_price, qty, "
        " notional_usd, opened_at, status) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
        "RETURNING id",
        bot_id,
        order_id,
        "BTCUSDT",
        "buy",
        Decimal("65000"),
        Decimal("0.001"),
        Decimal("65.0000"),
        datetime(2026, 5, 7, 12, 0, 1, tzinfo=UTC),
        "open",
    )
    assert isinstance(trade_id, int)
    return trade_id


async def test_migration_0014_creates_shadow_variants_table(
    migrated_db_dsn: str,
) -> None:
    """14-column shape verbatim per T-510a §Schema-design."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'shadow_variants' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_VARIANTS_COLUMNS
    finally:
        await conn.close()


async def test_migration_0014_creates_shadow_rejected_table(
    migrated_db_dsn: str,
) -> None:
    """11-column shape verbatim per T-510a §Schema-design."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'shadow_rejected' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_REJECTED_COLUMNS
    finally:
        await conn.close()


async def test_migration_0014_creates_btree_indexes(
    migrated_db_dsn: str,
) -> None:
    """4 btree indexes present (parent + bot_active partial; signal + bot_active partial)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        index_names = [
            row["indexname"]
            for row in await conn.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND tablename IN ('shadow_variants', 'shadow_rejected')"
            )
        ]
        assert "shadow_variants_parent" in index_names
        assert "shadow_variants_bot_active" in index_names
        assert "shadow_rejected_signal" in index_names
        assert "shadow_rejected_bot_active" in index_names
    finally:
        await conn.close()


async def test_migration_0014_shadow_variants_fk_cascade_to_trades(
    migrated_db_dsn: str,
) -> None:
    """OQ-3=A — DELETE parent trades row cascade-deletes child shadow_variants."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        trade_id = await _insert_trade(conn)
        await conn.execute(
            "INSERT INTO shadow_variants "
            "(parent_trade_id, bot_id, variant_name, side, "
            " entry_price, qty, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            trade_id,
            "alpha",
            "no_be",
            "buy",
            Decimal("65000"),
            Decimal("0.001"),
            datetime(2026, 5, 7, 12, 0, 2, tzinfo=UTC),
        )
        before = await conn.fetchval(
            "SELECT COUNT(*) FROM shadow_variants WHERE parent_trade_id = $1", trade_id
        )
        assert before == 1

        await conn.execute("DELETE FROM trades WHERE id = $1", trade_id)

        after = await conn.fetchval(
            "SELECT COUNT(*) FROM shadow_variants WHERE parent_trade_id = $1", trade_id
        )
        assert after == 0
    finally:
        await conn.close()


async def test_migration_0014_shadow_rejected_no_fk_on_signal_id(
    migrated_db_dsn: str,
) -> None:
    """OQ-6=A — shadow_rejected.signal_id has NO FK; arbitrary signal_id INSERT succeeds."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # Catalog assertion: zero FK constraints on shadow_rejected referencing signals.
        fk_count = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_constraint "
            "WHERE conrelid = 'shadow_rejected'::regclass "
            "  AND contype = 'f'"
        )
        assert fk_count == 0

        # Runtime assertion: arbitrary signal_id (no parent in signals table) inserts cleanly.
        arbitrary_signal_id = 999_999_999
        await conn.execute(
            "INSERT INTO shadow_rejected "
            "(signal_id, bot_id, symbol, would_side, created_at) "
            "VALUES ($1, $2, $3, $4, $5)",
            arbitrary_signal_id,
            "alpha",
            "BTCUSDT",
            "buy",
            datetime(2026, 5, 7, 12, 5, 0, tzinfo=UTC),
        )
        inserted = await conn.fetchval(
            "SELECT COUNT(*) FROM shadow_rejected WHERE signal_id = $1", arbitrary_signal_id
        )
        assert inserted == 1
    finally:
        await conn.close()


async def test_migration_0014_tables_are_not_hypertables(
    migrated_db_dsn: str,
) -> None:
    """OQ-2=A — both shadow tables are plain (NOT hypertable) per §7.2:838 authoritative list."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM _timescaledb_catalog.hypertable "
            "WHERE table_name IN ('shadow_variants', 'shadow_rejected')"
        )
        assert rows == []
    finally:
        await conn.close()


async def test_migration_0014_meta_defaults_are_empty_jsonb(
    migrated_db_dsn: str,
) -> None:
    """OQ-5=A — meta column has server-default '{}'::jsonb on both tables."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # shadow_variants: insert without meta → server-default applies.
        trade_id = await _insert_trade(conn)
        variant_id = await conn.fetchval(
            "INSERT INTO shadow_variants "
            "(parent_trade_id, bot_id, variant_name, side, "
            " entry_price, qty, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "RETURNING id",
            trade_id,
            "alpha",
            "baseline",
            "buy",
            Decimal("65000"),
            Decimal("0.001"),
            datetime(2026, 5, 7, 12, 0, 3, tzinfo=UTC),
        )
        variants_meta_raw = await conn.fetchval(
            "SELECT meta FROM shadow_variants WHERE id = $1", variant_id
        )
        # asyncpg without registered JSONB codec returns JSONB as str — explicit
        # json.loads decode (option (b) per T-501 plan §Test strategy + L-011 read-side).
        assert json.loads(variants_meta_raw) == {}

        # shadow_rejected: insert without meta → server-default applies.
        rejected_id = await conn.fetchval(
            "INSERT INTO shadow_rejected "
            "(signal_id, bot_id, symbol, would_side, created_at) "
            "VALUES ($1, $2, $3, $4, $5) "
            "RETURNING id",
            42,
            "alpha",
            "BTCUSDT",
            "buy",
            datetime(2026, 5, 7, 12, 0, 4, tzinfo=UTC),
        )
        rejected_meta_raw = await conn.fetchval(
            "SELECT meta FROM shadow_rejected WHERE id = $1", rejected_id
        )
        assert json.loads(rejected_meta_raw) == {}
    finally:
        await conn.close()


async def test_migration_0014_downgrade_drops_both_tables(
    base_dsn: str,
) -> None:
    """§N8 + L-012 — explicit downgrade 0013 target drops both shadow tables; parents preserved."""
    throwaway_name = f"scalper_v2_mig0014_dn_{_uuid_mod.uuid4().hex[:8]}"

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
        # Explicit target 0013 per L-012 — robust against future migrations
        # changing alembic head (relative -1 would silently rollback the wrong
        # revision once 0015+ lands and shadow_variants/rejected drop assertions
        # would still pass while 0014 stays effectively un-tested for downgrade).
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "downgrade", "0013"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": throwaway_dsn},
            cwd=_REPO_ROOT,
        )

        conn = await asyncpg.connect(dsn=throwaway_dsn)
        try:
            # Both shadow tables dropped.
            for table in ("shadow_variants", "shadow_rejected"):
                row = await conn.fetchrow(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = $1",
                    table,
                )
                assert row is None, f"{table} should be dropped after downgrade 0013"

            # Parent tables preserved (trades, signals, backtest_runs all from earlier migrations).
            for table in ("trades", "signals", "backtest_runs"):
                row = await conn.fetchrow(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = $1",
                    table,
                )
                assert row is not None, f"{table} should be preserved (earlier migration)"

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
