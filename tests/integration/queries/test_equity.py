"""Integration tests for :mod:`packages.db.queries.equity` (T-531).

Runs against a throwaway PostgreSQL + TimescaleDB migrated to head
(includes 0019 bot_equity_snapshots). Real-PG round-trip verifies:

* the 7 ``$N`` bind slots land in the right columns (mock-only can't catch
  off-by-one $N ordering — L-008 active control);
* surrogate ``id`` auto-populates via ``Identity(always=False)`` (omitted
  from INSERT);
* the documented Gate-4 boundary (a): an unbounded T-530 ``Decimal``
  (``125000.12345678``) is round-half-even truncated to ``NUMERIC(20,4)``
  (→ ``125000.1235``) at INSERT — proves the scale-4 persist behaviour the
  plan's Hand verification asserts.

Skipped at collection when ``POSTGRES_TEST_DSN`` is unset.

Per L-021 active control: MUST be run locally with
``POSTGRES_TEST_DSN=... uv run pytest tests/integration/queries/test_equity.py -v``
BEFORE git push (CI must not be the first execution surface).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import pytest

from packages.db.queries.equity import insert_equity_snapshot

_SNAP = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_insert_equity_snapshot_round_trip_bind_order(
    migrated_db_dsn: str,
) -> None:
    """7 bind slots → correct columns; surrogate id auto-populates."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await insert_equity_snapshot(
            conn,
            bot_id="alpha",
            snapshot_at=_SNAP,
            wallet_balance=Decimal("10250.5000"),
            available_balance=Decimal("10100.2500"),
            total_equity=Decimal("10250.5000"),
            margin_balance=Decimal("150.2500"),
            unrealized_pnl=Decimal("-125.2500"),
        )
        row = await conn.fetchrow(
            "SELECT id, bot_id, snapshot_at, wallet_balance, available_balance, "
            "total_equity, margin_balance, unrealized_pnl "
            "FROM bot_equity_snapshots WHERE bot_id = $1",
            "alpha",
        )
        assert row is not None
        assert row["id"] is not None  # surrogate Identity auto-populated
        assert row["bot_id"] == "alpha"
        assert row["snapshot_at"] == _SNAP
        assert row["wallet_balance"] == Decimal("10250.5000")
        assert row["available_balance"] == Decimal("10100.2500")
        assert row["total_equity"] == Decimal("10250.5000")
        assert row["margin_balance"] == Decimal("150.2500")
        assert row["unrealized_pnl"] == Decimal("-125.2500")
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_unbounded_decimal_round_half_even_to_scale_4(
    migrated_db_dsn: str,
) -> None:
    """Gate-4 boundary (a): NUMERIC(20,4) round-half-even at INSERT.

    Bybit "125000.12345678" → AccountBalance Decimal → stored 125000.1235
    (Hand verification truncation example). NOT a silent degradation —
    monitoring time-series; financial truth = T-220 cumulative-delta audit.
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await insert_equity_snapshot(
            conn,
            bot_id="trunc",
            snapshot_at=_SNAP,
            wallet_balance=Decimal("125000.12345678"),
            available_balance=Decimal("125000.12345678"),
            total_equity=Decimal("125000.12345678"),
            margin_balance=Decimal("125000.12345678"),
            unrealized_pnl=Decimal("0"),
        )
        stored = await conn.fetchval(
            "SELECT total_equity FROM bot_equity_snapshots WHERE bot_id = $1",
            "trunc",
        )
        assert stored == Decimal("125000.1235")
    finally:
        await conn.close()
