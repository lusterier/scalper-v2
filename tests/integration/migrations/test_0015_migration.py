"""Integration test for migration 0015 (T-511b2a / ADR-0010).

Runs ``alembic upgrade head`` against a throwaway database and verifies
migration 0015 deltas:

* ``shadow_variants_parent_trade_id_fkey`` FK is dropped (was added 0014).
* ``parent_kind: TEXT NOT NULL`` column is added with ``column_default IS NULL``
  post-upgrade (verifies the WG#1 two-step ALTER pattern: ``server_default='live'``
  was applied + dropped after NOT NULL).
* INSERTs with arbitrary ``parent_trade_id`` succeed regardless of FK existence
  in either ``trades`` or ``paper_trades`` (no DB-layer enforcement; integrity
  at app layer via ``parent_kind`` discriminator + BotConfig.exchange.mode
  single-source-of-truth).
* Explicit ``downgrade 0014`` target per L-012 (NEVER relative ``-1`` — robust
  against future migrations changing alembic head when 0016 lands).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset (mirror 0014).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid as _uuid_mod
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


async def test_migration_0015_drops_parent_trade_id_fk(
    migrated_db_dsn: str,
) -> None:
    """ADR-0010: shadow_variants_parent_trade_id_fkey FK is dropped post-upgrade."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        fk_row = await conn.fetchrow(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'shadow_variants'::regclass "
            "  AND contype = 'f' AND conname = 'shadow_variants_parent_trade_id_fkey'"
        )
        assert fk_row is None, (
            "0015 should drop shadow_variants_parent_trade_id_fkey; FK still present"
        )
    finally:
        await conn.close()


async def test_migration_0015_adds_parent_kind_column_not_null(
    migrated_db_dsn: str,
) -> None:
    """parent_kind TEXT NOT NULL exists + column_default IS NULL post-upgrade.

    WG#1: two-step ALTER pattern (``add_column server_default='live' nullable=False``
    → ``alter_column server_default=None``) — assert ``column_default IS NULL``
    post-upgrade so a future review skipping the second step is caught
    (CONCERN 2 fix).
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'shadow_variants' "
            "  AND column_name = 'parent_kind'"
        )
        assert row is not None, "parent_kind column missing"
        assert row["data_type"] == "text"
        assert row["is_nullable"] == "NO"
        # Two-step ALTER verification: server_default was dropped after NOT NULL applied.
        assert row["column_default"] is None, (
            "parent_kind column_default should be NULL post-upgrade "
            "(WG#1 two-step ALTER: server_default='live' was dropped after NOT NULL)"
        )
    finally:
        await conn.close()


async def test_migration_0015_inserts_with_paper_kind_succeed_no_fk(
    migrated_db_dsn: str,
) -> None:
    """Arbitrary parent_trade_id INSERT with parent_kind='paper' succeeds (no FK enforcement)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # Arbitrary parent_trade_id — neither in trades nor in paper_trades.
        # FK was dropped in 0015; INSERT should succeed regardless.
        arbitrary_parent_id = 999_999_999
        await conn.execute(
            "INSERT INTO shadow_variants "
            "(parent_trade_id, bot_id, variant_name, side, "
            " entry_price, qty, created_at, parent_kind) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            arbitrary_parent_id,
            "alpha",
            "no_be",
            "buy",
            Decimal("65000"),
            Decimal("0.001"),
            datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC),
            "paper",
        )
        inserted = await conn.fetchval(
            "SELECT COUNT(*) FROM shadow_variants WHERE parent_trade_id = $1",
            arbitrary_parent_id,
        )
        assert inserted == 1
    finally:
        await conn.close()


async def test_migration_0015_inserts_with_live_kind_no_fk_violation_when_orphan(
    migrated_db_dsn: str,
) -> None:
    """parent_kind='live' + orphan parent_trade_id (no trades row) inserts cleanly post-FK-drop."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # parent_kind='live' + orphan parent_trade_id (would have violated 0014 FK).
        orphan_id = 888_888_888
        await conn.execute(
            "INSERT INTO shadow_variants "
            "(parent_trade_id, bot_id, variant_name, side, "
            " entry_price, qty, created_at, parent_kind) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            orphan_id,
            "alpha",
            "no_be",
            "buy",
            Decimal("65000"),
            Decimal("0.001"),
            datetime(2026, 5, 8, 12, 0, 1, tzinfo=UTC),
            "live",
        )
        inserted = await conn.fetchval(
            "SELECT COUNT(*) FROM shadow_variants WHERE parent_trade_id = $1",
            orphan_id,
        )
        assert inserted == 1
    finally:
        await conn.close()


async def test_migration_0015_downgrade_re_adds_fk_drops_parent_kind(
    base_dsn: str,
) -> None:
    """§N8 + L-012 — explicit downgrade 0014 target re-adds FK + drops parent_kind."""
    throwaway_name = f"scalper_v2_mig0015_dn_{_uuid_mod.uuid4().hex[:8]}"

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
        # Explicit target 0014 per L-012 — robust against future migrations
        # changing alembic head once 0016+ lands.
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "downgrade", "0014"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "POSTGRES_URL": throwaway_dsn},
            cwd=_REPO_ROOT,
        )

        conn = await asyncpg.connect(dsn=throwaway_dsn)
        try:
            # FK re-added after downgrade.
            fk_row = await conn.fetchrow(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'shadow_variants'::regclass "
                "  AND contype = 'f' "
                "  AND conname = 'shadow_variants_parent_trade_id_fkey'"
            )
            assert fk_row is not None, "downgrade should re-add FK"

            # parent_kind column dropped after downgrade.
            col_row = await conn.fetchrow(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'shadow_variants' "
                "  AND column_name = 'parent_kind'"
            )
            assert col_row is None, "downgrade should drop parent_kind column"
        finally:
            await conn.close()
    finally:
        admin_conn = await asyncpg.connect(dsn=base_dsn)
        try:
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{throwaway_name}" WITH (FORCE)')
        finally:
            await admin_conn.close()
