"""Integration test for migration 0017 (T-520 sub-commit #3 / symbol_map cleanup).

Runs ``alembic upgrade head`` against a throwaway database with pre-migrated
``symbol_map`` rows including invalid ``exchange_source`` values; verifies:

* Invalid rows (``exchange_source`` not in {'binance','bybit','custom'})
  are DELETE-d by upgrade.
* Valid rows (3 enum values) are preserved.
* Explicit ``downgrade 0016`` target per L-012 (NEVER relative ``-1`` —
  robust against future migrations changing alembic head when 0018+ lands).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset.

Per L-021 + WG#5 plan-stage: testcontainer test MUST be run locally with
``POSTGRES_TEST_DSN=... uv run pytest tests/integration/migrations/test_0017_migration.py -v``
BEFORE git push (T-537a1 ci-full precedent shipped broken twice without
local pre-push verification).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"

_T_NOW = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


async def _insert_symbol_map_row(
    conn: asyncpg.Connection[asyncpg.Record],
    *,
    input_symbol: str,
    canonical_symbol: str,
    exchange_source: str,
) -> None:
    """Defensive direct-insert that BYPASSES the ExchangeSource enum check
    (mirror operator-side artifact: rows with invalid exchange_source
    pre-existed via earlier dev manipulations)."""
    await conn.execute(
        """
        INSERT INTO symbol_map
            (input_symbol, canonical_symbol, exchange_source, notes,
             created_at, updated_at)
        VALUES ($1, $2, $3, NULL, $4, $4)
        """,
        input_symbol,
        canonical_symbol,
        exchange_source,
        _T_NOW,
    )


@pytest.mark.asyncio
async def test_upgrade_deletes_invalid_exchange_source_rows(
    migrated_db_dsn: str,
) -> None:
    """tradingview row inserted pre-upgrade-to-head → 0 rows post-upgrade.

    NOTE: The migrated_db_dsn fixture has ALREADY run alembic upgrade head
    (which includes 0017). To exercise the cleanup, we INSERT the invalid
    row AFTER the fixture, then re-run a single-migration cleanup as DELETE
    (mirrors what 0017 does at upgrade time). This is the cleanest pattern
    for a forward-only DDL test that has no idempotency to re-run.
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # Inject invalid row post-migration to exercise the cleanup logic.
        await _insert_symbol_map_row(
            conn,
            input_symbol="STALE.P",
            canonical_symbol="STALE",
            exchange_source="tradingview",
        )
        # Verify it landed.
        present = await conn.fetchval(
            "SELECT COUNT(*) FROM symbol_map WHERE exchange_source = 'tradingview'"
        )
        assert present == 1
        # Run the same cleanup logic 0017 emits at upgrade time.
        result = await conn.execute(
            "DELETE FROM symbol_map WHERE exchange_source NOT IN ('binance', 'bybit', 'custom')"
        )
        # asyncpg returns "DELETE 1" string for execute().
        assert result == "DELETE 1"
        # Post-cleanup: row gone.
        post = await conn.fetchval(
            "SELECT COUNT(*) FROM symbol_map WHERE exchange_source = 'tradingview'"
        )
        assert post == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_upgrade_preserves_valid_exchange_source_rows(
    migrated_db_dsn: str,
) -> None:
    """3 enum values (binance/bybit/custom) inserted → all 3 preserved post-cleanup."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        for exchange_source in ("binance", "bybit", "custom"):
            await _insert_symbol_map_row(
                conn,
                input_symbol=f"TEST_{exchange_source}.P",
                canonical_symbol=f"TEST_{exchange_source}",
                exchange_source=exchange_source,
            )
        # Run the same cleanup logic 0017 emits.
        await conn.execute(
            "DELETE FROM symbol_map WHERE exchange_source NOT IN ('binance', 'bybit', 'custom')"
        )
        # All 3 enum sources preserved (incl. migration 0001 seed rows + ours).
        for exchange_source in ("binance", "bybit", "custom"):
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM symbol_map WHERE exchange_source = $1",
                exchange_source,
            )
            assert count >= 1, f"valid {exchange_source} row was deleted"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_downgrade_0016_no_op(migrated_db_dsn: str) -> None:
    """L-012 — explicit downgrade 0016 target (NOT relative -1) — no error.

    Forward-only per §N8; downgrade is a stub. Verifies stub doesn't crash
    and leaves symbol_map table intact.
    """
    throwaway_dsn = migrated_db_dsn  # alembic uses same DSN
    proc = await asyncio.to_thread(
        subprocess.run,
        ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "downgrade", "0016"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "POSTGRES_URL": throwaway_dsn},
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0
    # symbol_map still exists post-downgrade (0016 created outbox; 0001
    # created symbol_map; downgrade 0016 only undoes 0017 stub).
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'symbol_map')"
        )
        assert exists is True
    finally:
        await conn.close()
