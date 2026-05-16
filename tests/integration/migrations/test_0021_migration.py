"""Integration test for migration 0021 (T-532a / funding_fees).

Runs against a throwaway DB already migrated to head (includes 0021). Verifies:

* ``funding_fees`` exists with the exact 5-column shape (surrogate ``id`` +
  ``bot_id`` + ``symbol`` + ``settled_at`` + ``funding``), all NOT NULL.
* ``funding`` is ``NUMERIC(20, 4)`` — repo USD-money/P&L convention. Per
  L-005 (sa.Float-vs-sa.Double silent-precision-degradation lesson family —
  ``information_schema.columns.data_type`` returns ``"numeric"`` for BOTH
  bare ``Numeric()`` and ``Numeric(20,4)``), a separate
  ``numeric_precision``/``numeric_scale`` assertion is the regression
  tripwire against a future revert to bare ``Numeric()``.
* PRIMARY KEY ``(settled_at, id)`` composite (TimescaleDB partition column
  in PK) + NO foreign key (hypertable-sibling convention).
* Hypertable with 7-day ``chunk_time_interval``.
* Explicit ``downgrade 0020`` target per L-012 (NEVER relative ``-1`` —
  robust against future migrations changing alembic head when 0022+ lands);
  downgrade drops the table.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset.

Per L-021 active control: this testcontainer test MUST be run locally with
``POSTGRES_TEST_DSN=... uv run pytest tests/integration/migrations/test_0021_migration.py -v``
BEFORE git push (T-537a1 ci-full precedent shipped broken twice without
local pre-push verification — CI must not be the first execution surface).
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
    "id": "bigint",
    "bot_id": "text",
    "symbol": "text",
    "settled_at": "timestamp with time zone",
    "funding": "numeric",
}


@pytest.mark.asyncio
async def test_upgrade_creates_funding_fees_table(
    migrated_db_dsn: str,
) -> None:
    """Head migration (incl. 0021) → table with exact 5-column shape, all NOT NULL."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'funding_fees'
            ORDER BY ordinal_position
            """
        )
        got = {r["column_name"]: r["data_type"] for r in rows}
        assert got == _EXPECTED_COLUMNS, f"column shape drift: {got}"
        nullability = {r["column_name"]: r["is_nullable"] for r in rows}
        assert all(v == "NO" for v in nullability.values()), nullability
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_funding_column_numeric_precision_scale_20_4(
    migrated_db_dsn: str,
) -> None:
    """L-005 tripwire: ``funding`` is NUMERIC(20,4) — data_type alone
    ("numeric" for bare Numeric() too) would NOT catch a precision regress."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_name = 'funding_fees' AND column_name = 'funding'
            """
        )
        assert row is not None
        assert (row["numeric_precision"], row["numeric_scale"]) == (20, 4), (
            f"precision/scale drift (expected NUMERIC(20,4)): {dict(row)}"
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_pk_composite_settled_at_id_and_no_fk(migrated_db_dsn: str) -> None:
    """Composite PK (settled_at, id); NO foreign key (hypertable sibling)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        pk_cols = await conn.fetch(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_name = 'funding_fees'
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
            """
        )
        assert [r["column_name"] for r in pk_cols] == ["settled_at", "id"]
        fk_count = await conn.fetchval(
            """
            SELECT count(*) FROM information_schema.table_constraints
            WHERE table_name = 'funding_fees'
              AND constraint_type = 'FOREIGN KEY'
            """
        )
        assert fk_count == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_hypertable_7day_chunk(migrated_db_dsn: str) -> None:
    """TimescaleDB hypertable on settled_at with 7-day chunk_time_interval."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        hypertable_row = await conn.fetchrow(
            """
            SELECT h.table_name, d.interval_length
            FROM _timescaledb_catalog.hypertable h
            JOIN _timescaledb_catalog.dimension d ON d.hypertable_id = h.id
            WHERE h.table_name = 'funding_fees'
            """
        )
        assert hypertable_row is not None
        # interval_length is microseconds; 7 days = 7 * 86400 * 1_000_000.
        assert hypertable_row["interval_length"] == 7 * 86400 * 1_000_000
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_downgrade_0020_drops_table(migrated_db_dsn: str) -> None:
    """L-012 explicit downgrade 0020 target (NOT relative -1) → table dropped."""
    proc = await asyncio.to_thread(
        subprocess.run,
        ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "downgrade", "0020"],
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
            "WHERE table_name = 'funding_fees')"
        )
        assert exists is False
    finally:
        await conn.close()
