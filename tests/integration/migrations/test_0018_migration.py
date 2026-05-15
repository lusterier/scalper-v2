"""Integration test for migration 0018 (T-525a1 / bot_kill_switch_state, H-027).

Runs against a throwaway DB already migrated to head (includes 0018). Verifies:

* ``bot_kill_switch_state`` exists with the exact 7-column shape + PK(bot_id)
  + FK(bot_id → bots.bot_id).
* No ``server_default`` time on any TIMESTAMPTZ column (§N1 / WG#4) — the only
  column default is the boolean constant ``false`` on ``tripped``.
* Explicit ``downgrade 0017`` target per L-012 (NEVER relative ``-1`` — robust
  against future migrations changing alembic head when 0019+ lands); downgrade
  drops the table.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset.

Per L-021 + WG#7 (HARD AC#9): this testcontainer test MUST be run locally with
``POSTGRES_TEST_DSN=... uv run pytest tests/integration/migrations/test_0018_migration.py -v``
BEFORE git push (T-537a1 ci-full precedent shipped broken twice without local
pre-push verification — CI must not be the first execution surface).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import asyncpg
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"

_EXPECTED_COLUMNS: dict[str, str] = {
    "bot_id": "text",
    "tripped": "boolean",
    "trip_reason": "text",
    "tripped_at": "timestamp with time zone",
    "daily_anchor_date": "date",
    "cumulative_loss_usd": "numeric",
    "updated_at": "timestamp with time zone",
}


@pytest.mark.asyncio
async def test_upgrade_creates_bot_kill_switch_state_table(
    migrated_db_dsn: str,
) -> None:
    """Head migration (incl. 0018) → table with exact 7-column shape."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'bot_kill_switch_state'
            ORDER BY ordinal_position
            """
        )
        got = {r["column_name"]: r["data_type"] for r in rows}
        assert got == _EXPECTED_COLUMNS, f"column shape drift: {got}"
        nullability = {r["column_name"]: r["is_nullable"] for r in rows}
        assert nullability["bot_id"] == "NO"
        assert nullability["tripped"] == "NO"
        assert nullability["updated_at"] == "NO"
        assert nullability["trip_reason"] == "YES"
        assert nullability["tripped_at"] == "YES"
        assert nullability["daily_anchor_date"] == "YES"
        assert nullability["cumulative_loss_usd"] == "YES"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_pk_and_fk_to_bots(migrated_db_dsn: str) -> None:
    """PK(bot_id) + FK(bot_id → bots.bot_id) present."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        pk = await conn.fetchval(
            """
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'bot_kill_switch_state' AND constraint_type = 'PRIMARY KEY'
            """
        )
        assert pk == "bot_kill_switch_state_pkey"
        fk = await conn.fetchval(
            """
            SELECT ccu.table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
            WHERE tc.table_name = 'bot_kill_switch_state'
              AND tc.constraint_type = 'FOREIGN KEY'
            """
        )
        assert fk == "bots"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_no_server_default_time_on_timestamptz(migrated_db_dsn: str) -> None:
    """§N1 / WG#4: no NOW()/CURRENT_TIMESTAMP server_default on any column;
    the only default is the boolean constant false on `tripped`."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT column_name, column_default
            FROM information_schema.columns
            WHERE table_name = 'bot_kill_switch_state' AND column_default IS NOT NULL
            """
        )
        defaults = {r["column_name"]: (r["column_default"] or "") for r in rows}
        # tripped → false constant; nothing time-based anywhere.
        for col, dflt in defaults.items():
            assert "now(" not in dflt.lower(), f"{col} has time server_default {dflt!r}"
            assert "current_timestamp" not in dflt.lower(), f"{col}: {dflt!r}"
        assert "false" in defaults.get("tripped", "").lower()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_downgrade_0017_drops_table(migrated_db_dsn: str) -> None:
    """L-012 explicit downgrade 0017 target (NOT relative -1) → table dropped."""
    proc = await asyncio.to_thread(
        subprocess.run,
        ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "downgrade", "0017"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "POSTGRES_URL": migrated_db_dsn},
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'bot_kill_switch_state')"
        )
        assert exists is False
    finally:
        await conn.close()
