"""Integration test for migration 0009 (T-220a — brief §9.5:1601-1605, H-017, ADR-0007 D7).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the ``trade_pnl_deltas`` hypertable landed exactly as specified.

Schema lock-site per T-200 Q6:

* 8 columns: ``id BIGSERIAL``, ``sub_account TEXT NOT NULL``, 3 TIMESTAMPTZ
  fields (``audit_run_at``, ``window_start``, ``window_end``), 3 NUMERIC(20,4)
  fields (``cumulative_bybit``, ``cumulative_db``, ``delta``).
* Composite PK ``(audit_run_at, id)`` per TimescaleDB hypertable convention.
* UNIQUE ``(sub_account, audit_run_at)`` per ADR-0007 D7 belt-and-suspenders.
* Hypertable on ``audit_run_at`` with 7-day chunks.
* Index ``ix_trade_pnl_deltas_sub_account_audit (sub_account, audit_run_at DESC)``.
* No FK on any column (audit-stream stands alone; sub_account is opaque string).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import pytest

_EXPECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("sub_account", "text", "NO"),
    ("audit_run_at", "timestamp with time zone", "NO"),
    ("window_start", "timestamp with time zone", "NO"),
    ("window_end", "timestamp with time zone", "NO"),
    ("cumulative_bybit", "numeric", "NO"),
    ("cumulative_db", "numeric", "NO"),
    ("delta", "numeric", "NO"),
)

_T_AUDIT = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
_T_WINDOW_START = datetime(2026, 5, 2, 9, 0, 0, tzinfo=UTC)
_T_WINDOW_END = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


async def test_migration_0009_creates_trade_pnl_deltas_hypertable(
    migrated_db_dsn: str,
) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # (a) Column shape per T-200 Q6 schema lock.
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'trade_pnl_deltas' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_COLUMNS

        # (b) Composite PK (audit_run_at, id).
        pk_columns = [
            row["column_name"]
            for row in await conn.fetch(
                """
                SELECT a.attname AS column_name
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'p'
                  AND c.conrelid = 'public.trade_pnl_deltas'::regclass
                ORDER BY array_position(c.conkey, a.attnum)
                """
            )
        ]
        assert pk_columns == ["audit_run_at", "id"]

        # (c) UNIQUE constraint (sub_account, audit_run_at).
        unique_constraint = await conn.fetchrow(
            """
            SELECT c.conname,
                   array_agg(a.attname ORDER BY array_position(c.conkey, a.attnum)) AS cols
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE c.contype = 'u'
              AND c.conrelid = 'public.trade_pnl_deltas'::regclass
              AND c.conname = 'uq_trade_pnl_deltas_sub_account_audit_run_at'
            GROUP BY c.conname
            """
        )
        assert unique_constraint is not None
        assert list(unique_constraint["cols"]) == ["sub_account", "audit_run_at"]

        # (d) Hypertable + 7-day chunk_time_interval.
        hypertable_row = await conn.fetchrow(
            """
            SELECT h.table_name, d.interval_length
            FROM _timescaledb_catalog.hypertable h
            JOIN _timescaledb_catalog.dimension d ON d.hypertable_id = h.id
            WHERE h.table_name = 'trade_pnl_deltas'
            """
        )
        assert hypertable_row is not None
        # interval_length is microseconds; 7 days = 7 * 86400 * 1_000_000.
        assert hypertable_row["interval_length"] == 7 * 86400 * 1_000_000

        # (e) Index ix_trade_pnl_deltas_sub_account_audit.
        index_def = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "AND tablename = 'trade_pnl_deltas' "
            "AND indexname = 'ix_trade_pnl_deltas_sub_account_audit'"
        )
        assert index_def is not None
        assert "sub_account" in index_def
        assert "audit_run_at" in index_def

        # (f) Decimal precision smoke INSERT + SELECT round-trip.
        await conn.execute(
            """
            INSERT INTO trade_pnl_deltas (
                sub_account, audit_run_at, window_start, window_end,
                cumulative_bybit, cumulative_db, delta
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            "alpha-sub",
            _T_AUDIT,
            _T_WINDOW_START,
            _T_WINDOW_END,
            Decimal("100.1234"),
            Decimal("99.6234"),
            Decimal("0.5000"),
        )
        row = await conn.fetchrow(
            "SELECT cumulative_bybit, cumulative_db, delta "
            "FROM trade_pnl_deltas WHERE sub_account = $1",
            "alpha-sub",
        )
        assert row is not None
        assert row["cumulative_bybit"] == Decimal("100.1234")
        assert row["cumulative_db"] == Decimal("99.6234")
        assert row["delta"] == Decimal("0.5000")

        # (g) UNIQUE constraint violation on duplicate (sub_account, audit_run_at).
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO trade_pnl_deltas (
                    sub_account, audit_run_at, window_start, window_end,
                    cumulative_bybit, cumulative_db, delta
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                "alpha-sub",
                _T_AUDIT,
                _T_WINDOW_START,
                _T_WINDOW_END,
                Decimal("0"),
                Decimal("0"),
                Decimal("0"),
            )
    finally:
        await conn.close()
