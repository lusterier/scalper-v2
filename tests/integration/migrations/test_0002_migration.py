"""Integration test for migration 0002 (brief §N8, §7.2, §7.4).

Runs ``alembic upgrade head`` against a throwaway database and
verifies the ``signals`` hypertable landed exactly as specified in
§7.2:

* ``signals`` table exists with the 11 columns from §7.2 in ordinal
  order and with the expected nullability.
* Primary key is the composite ``(received_at, id)`` required for
  TimescaleDB partitioning on ``received_at``.
* ``signals`` is registered as a TimescaleDB hypertable with a
  7-day ``chunk_time_interval``.
* All three §7.2 indexes exist with the exact definitions the brief
  specifies — UNIQUE ``(idempotency_key, received_at)``,
  ``(symbol, received_at DESC)``, and GIN ``(payload)``.
* The ``alembic_version`` row reports revision ``0002`` (so 0001
  ran transitively).

When a subsequent migration alters ``signals`` (new column, new
index, retention policy, continuous aggregate, etc.) that migration's
own test file asserts the *delta*; this file continues to assert the
post-0002 shape. If the operator ever re-bases or squashes 0002, the
column list / PK / index assertions below need updating to the new
baseline — the shape baked in here is deliberately explicit so that
kind of edit fails loudly rather than silently drifting.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg

_EXPECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (column_name, data_type, is_nullable) — information_schema.columns
    # flavours: TIMESTAMP WITH TIME ZONE is reported as "timestamp with
    # time zone"; JSONB is "jsonb"; TEXT is "text"; BIGINT is "bigint".
    ("id", "bigint", "NO"),
    ("received_at", "timestamp with time zone", "NO"),
    ("schema_version", "text", "NO"),
    ("source", "text", "NO"),
    ("idempotency_key", "text", "NO"),
    ("symbol", "text", "NO"),
    ("original_symbol", "text", "YES"),
    ("action", "text", "NO"),
    ("payload", "jsonb", "NO"),
    ("ingestion_status", "text", "NO"),
    ("correlation_id", "text", "NO"),
)


async def test_migration_0002_creates_signals_hypertable(migrated_db_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'signals' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_COLUMNS

        pk_columns = [
            row["column_name"]
            for row in await conn.fetch(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                " AND tc.table_schema = kcu.table_schema "
                "WHERE tc.table_schema = 'public' "
                "  AND tc.table_name = 'signals' "
                "  AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position"
            )
        ]
        assert pk_columns == ["received_at", "id"]

        hypertable = await conn.fetchrow(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = 'public' AND hypertable_name = 'signals'"
        )
        assert hypertable is not None, "signals must be registered as a hypertable"

        # TIMESTAMPTZ hypertables populate `time_interval` (PG interval →
        # datetime.timedelta via asyncpg); `integer_interval` is only set
        # for BIGINT/INTEGER time columns, which we do not use here.
        time_interval = await conn.fetchval(
            "SELECT time_interval FROM timescaledb_information.dimensions "
            "WHERE hypertable_schema = 'public' "
            "  AND hypertable_name = 'signals' "
            "  AND column_name = 'received_at'"
        )
        assert time_interval == timedelta(days=7), (
            f"expected 7-day chunk_time_interval, got {time_interval!r}"
        )

        index_defs = {
            row["indexname"]: row["indexdef"]
            for row in await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'signals'"
            )
        }
        assert "signals_idempotency" in index_defs
        assert "UNIQUE INDEX signals_idempotency" in index_defs["signals_idempotency"]
        assert "(idempotency_key, received_at)" in index_defs["signals_idempotency"]

        assert "signals_symbol_time" in index_defs
        assert "(symbol, received_at DESC)" in index_defs["signals_symbol_time"]

        assert "signals_payload_gin" in index_defs
        assert "USING gin (payload)" in index_defs["signals_payload_gin"]

        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version == "0002"
    finally:
        await conn.close()
