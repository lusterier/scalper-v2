"""Integration tests for :mod:`packages.db.queries.execution` (T-217a WG#19).

Runs against a throwaway PostgreSQL + TimescaleDB migrated to head
(includes T-202 orders/trades/executions FK chain + T-203 position_state).

Per L-008 active control: helpers exercising multi-column SET / non-trivial
SQL expressions (COALESCE/CAST/CASE/$N permutation) need a real-PG round-trip
because mock-only tests can't catch off-by-one $N bind ordering or PG
type-coercion failures.

Currently covers ``update_position_state_monitor_tick`` (T-217a) — the
helper has 7 bind sites (`SET best_price=$1, mfe_price=$2, mae_price=$3,
running_pnl=$4, updated_at=$5 WHERE bot_id=$6 AND symbol=$7`) and any
permutation of $N would pass the mock-only assertion that splits SQL on
``WHERE``. Round-trip verifies each column lands in the right slot.

Skipped at collection when ``POSTGRES_TEST_DSN`` is unset.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import pytest

from packages.db.queries.execution import (
    insert_order,
    insert_position_state,
    insert_trade,
    update_position_state_after_fill,
    update_position_state_monitor_tick,
)

_T_ENTRY = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
_T_TICK = datetime(2026, 5, 2, 12, 0, 5, tzinfo=UTC)


async def _seed_position_state(conn: asyncpg.Connection[asyncpg.Record]) -> None:
    """Set up bots → orders → trades → position_state row for tick test."""
    await conn.execute(
        "INSERT INTO bots "
        "(bot_id, display_name, created_at, status, exchange_mode, "
        " config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        "alpha",
        "Alpha Bot",
        _T_ENTRY,
        "active",
        "paper",
        "sha256:smoke",
        _T_ENTRY,
    )
    open_order_id = await insert_order(
        conn,
        bot_id="alpha",
        signal_id=1,
        correlation_id="cid-1",
        exchange_order_id="ord-1",
        exchange="bybit",
        symbol="BTCUSDT",
        side="buy",
        order_type="market",
        qty=Decimal("10"),
        price=Decimal("100"),
        status="filled",
        requested_at=_T_ENTRY,
        filled_at=_T_ENTRY,
        closed_at=None,
        idempotent_flag=False,
    )
    trade_id = await insert_trade(
        conn,
        bot_id="alpha",
        signal_id=1,
        open_order_id=open_order_id,
        symbol="BTCUSDT",
        side="buy",
        entry_price=Decimal("100"),
        qty=Decimal("10"),
        notional_usd=Decimal("1000"),
        opened_at=_T_ENTRY,
    )
    await insert_position_state(
        conn,
        bot_id="alpha",
        symbol="BTCUSDT",
        trade_id=trade_id,
        side="buy",
        entry_price=Decimal("100"),
        qty=Decimal("10"),
        remaining_qty=Decimal("10"),
        sl_price=Decimal("95"),
        tp_price=Decimal("110"),
        sl_type="protective",
        updated_at=_T_ENTRY,
    )


@pytest.mark.asyncio
async def test_update_position_state_monitor_tick_writes_correct_columns_against_real_pg(
    migrated_db_dsn: str,
) -> None:
    """L-008 / WG#19 — round-trip verifies $1..$7 bind ordering against real PG.

    Catches off-by-one bind permutation (e.g., swapped best_price ↔ mfe_price)
    that mock-only tests can't detect. Also pins NUMERIC(20,4) running_pnl
    rounding behavior at column-scale boundary (Decimal('0.1234') is 4-dp clean).
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await _seed_position_state(conn)
        await update_position_state_monitor_tick(
            conn,
            bot_id="alpha",
            symbol="BTCUSDT",
            best_price=Decimal("105.5"),
            mfe_price=Decimal("110"),
            mae_price=Decimal("98"),
            running_pnl=Decimal("0.1234"),
            updated_at=_T_TICK,
        )
        row = await conn.fetchrow(
            """
            SELECT best_price, mfe_price, mae_price, running_pnl, updated_at
            FROM position_state
            WHERE bot_id = $1 AND symbol = $2
            """,
            "alpha",
            "BTCUSDT",
        )
        assert row is not None
        assert row["best_price"] == Decimal("105.5")
        assert row["mfe_price"] == Decimal("110")
        assert row["mae_price"] == Decimal("98")
        assert row["running_pnl"] == Decimal("0.1234")
        assert row["updated_at"] == _T_TICK
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_update_position_state_after_fill_returns_zero_when_trade_id_mismatches(
    migrated_db_dsn: str,
) -> None:
    """T-217c / H-033 — composite-PK guard against close→reopen race.

    Seeds position_state row with trade_id=10; calls helper with trade_id=11
    (simulating late WS event for closed T1 arriving after T2 reopened on
    same `(bot_id, symbol)`). Helper returns 0 (no rows updated; WHERE
    clause anchored on trade_id rejected the mismatched id). Row state
    must remain UNCHANGED — remaining_qty stays at the seeded value, no
    silent corruption of T2's row.
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await _seed_position_state(conn)
        seeded_trade_id = await conn.fetchval(
            "SELECT trade_id FROM position_state WHERE bot_id = $1 AND symbol = $2",
            "alpha",
            "BTCUSDT",
        )
        mismatched_trade_id = seeded_trade_id + 1
        rows_updated = await update_position_state_after_fill(
            conn,
            bot_id="alpha",
            symbol="BTCUSDT",
            trade_id=mismatched_trade_id,
            qty_delta=Decimal("3"),
            new_sl_type=None,
            updated_at=_T_TICK,
        )
        assert rows_updated == 0
        row = await conn.fetchrow(
            """
            SELECT trade_id, remaining_qty, sl_type, updated_at
            FROM position_state
            WHERE bot_id = $1 AND symbol = $2
            """,
            "alpha",
            "BTCUSDT",
        )
        assert row is not None
        assert row["trade_id"] == seeded_trade_id  # T2's row identity unchanged
        assert row["remaining_qty"] == Decimal("10")  # not decremented
        assert row["sl_type"] == "protective"  # not mutated
        assert row["updated_at"] == _T_ENTRY  # not bumped
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_update_position_state_after_fill_returns_one_when_trade_id_matches(
    migrated_db_dsn: str,
) -> None:
    """T-217c / H-033 — happy-path round-trip: matching trade_id mutates the row.

    Companion to the mismatch test above; pins the positive path for the
    new SQL signature against real PG (catches potential $N bind ordering
    regression on the new trade_id parameter).
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await _seed_position_state(conn)
        seeded_trade_id = await conn.fetchval(
            "SELECT trade_id FROM position_state WHERE bot_id = $1 AND symbol = $2",
            "alpha",
            "BTCUSDT",
        )
        rows_updated = await update_position_state_after_fill(
            conn,
            bot_id="alpha",
            symbol="BTCUSDT",
            trade_id=seeded_trade_id,
            qty_delta=Decimal("4"),
            new_sl_type="trail",
            updated_at=_T_TICK,
        )
        assert rows_updated == 1
        row = await conn.fetchrow(
            """
            SELECT trade_id, remaining_qty, sl_type, updated_at
            FROM position_state
            WHERE bot_id = $1 AND symbol = $2
            """,
            "alpha",
            "BTCUSDT",
        )
        assert row is not None
        assert row["trade_id"] == seeded_trade_id
        assert row["remaining_qty"] == Decimal("6")  # 10 - 4
        assert row["sl_type"] == "trail"  # mutated per new_sl_type
        assert row["updated_at"] == _T_TICK
    finally:
        await conn.close()
