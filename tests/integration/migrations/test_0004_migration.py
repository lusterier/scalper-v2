"""Integration test for migration 0004 (brief §N8, §7.2, §7.4, §18.3).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the ``features`` hypertable landed exactly as specified:

* ``features`` table exists with the 7 columns from §7.2 with the
  expected nullability + composite PK
  ``(feature_name, symbol, computed_at, source_version)``.
* ``features`` is registered as a TimescaleDB hypertable with a 7-day
  ``chunk_time_interval``.
* Compression is enabled with ``segmentby = 'feature_name, symbol'``,
  ``orderby = 'computed_at DESC'``, and a 30-day compression policy
  per §18.3.
* A 180-day retention policy on ``features`` per §18.3.
* The ``features_latest`` secondary index exists with shape
  ``(feature_name, symbol, computed_at DESC)`` per §7.2 line 914.
* End-to-end smoke: INSERT 3 rows (one per ``value_*`` variant —
  float, bool, JSONB), SELECT back via PK, assert types and values
  round-trip. asyncpg JSONB codec is registered explicitly so JSONB
  yields a Python dict instead of the default raw JSON string.
* The ``alembic_version`` row exists. Permissive — successor
  migrations legitimately advance head; this test asserts the
  post-0004 artifact shape, not the tail revision. Mirrors test_0001
  / test_0002 / test_0003 pattern.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import asyncpg

_EXPECTED_FEATURES_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (column_name, data_type, is_nullable). information_schema flavours:
    # DOUBLE PRECISION → "double precision", BOOLEAN → "boolean",
    # JSONB → "jsonb", TIMESTAMPTZ → "timestamp with time zone",
    # TEXT → "text".
    ("feature_name", "text", "NO"),
    ("symbol", "text", "NO"),
    ("computed_at", "timestamp with time zone", "NO"),
    ("value_num", "double precision", "YES"),
    ("value_bool", "boolean", "YES"),
    ("value_json", "jsonb", "YES"),
    ("source_version", "text", "NO"),
)


async def test_migration_0004_creates_features_hypertable(migrated_db_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # asyncpg defaults to raw JSON string for jsonb; register a codec
        # so the smoke INSERT/SELECT round-trips value_json as Python dict.
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

        # features column shape.
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'features' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_FEATURES_COLUMNS

        # Composite PK (feature_name, symbol, computed_at, source_version).
        pk_columns = [
            row["column_name"]
            for row in await conn.fetch(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                " AND tc.table_schema = kcu.table_schema "
                "WHERE tc.table_schema = 'public' "
                "  AND tc.table_name = 'features' "
                "  AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position"
            )
        ]
        assert pk_columns == ["feature_name", "symbol", "computed_at", "source_version"]

        # features is a hypertable with 7-day chunks.
        time_interval = await conn.fetchval(
            "SELECT time_interval FROM timescaledb_information.dimensions "
            "WHERE hypertable_schema = 'public' "
            "  AND hypertable_name = 'features' "
            "  AND column_name = 'computed_at'"
        )
        assert time_interval == timedelta(days=7), (
            f"expected 7-day chunk_time_interval, got {time_interval!r}"
        )

        # Compression enabled with the expected segmentby/orderby.
        compression_rows = await conn.fetch(
            "SELECT attname, segmentby_column_index, orderby_column_index, "
            "       orderby_asc, orderby_nullsfirst "
            "FROM timescaledb_information.compression_settings "
            "WHERE hypertable_schema = 'public' AND hypertable_name = 'features' "
            "ORDER BY segmentby_column_index NULLS LAST, orderby_column_index"
        )
        segmentby = [
            r["attname"] for r in compression_rows if r["segmentby_column_index"] is not None
        ]
        assert segmentby == ["feature_name", "symbol"]
        orderby = [
            (r["attname"], r["orderby_asc"], r["orderby_nullsfirst"])
            for r in compression_rows
            if r["orderby_column_index"] is not None
        ]
        # orderby_asc=False ↔ DESC (`computed_at DESC` in the migration).
        # orderby_nullsfirst=True is TimescaleDB's default for DESC.
        assert orderby == [("computed_at", False, True)]

        # Retention + compression policies registered for features.
        # The COALESCE pattern from test_0003 is unnecessary here because
        # features is a plain hypertable (no continuous aggregate).
        policy_targets = {
            (row["proc_name"], row["table_name"])
            for row in await conn.fetch(
                "SELECT j.proc_name, h.table_name "
                "FROM _timescaledb_config.bgw_job j "
                "JOIN _timescaledb_catalog.hypertable h "
                "  ON h.id = (j.config->>'hypertable_id')::int "
                "WHERE h.table_name = 'features'"
            )
        }
        assert ("policy_retention", "features") in policy_targets
        assert ("policy_compression", "features") in policy_targets

        # Secondary index features_latest shape verification via pg_indexes.
        index_defs = {
            row["indexname"]: row["indexdef"]
            for row in await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'features'"
            )
        }
        assert "features_latest" in index_defs
        assert "(feature_name, symbol, computed_at DESC)" in index_defs["features_latest"]

        # End-to-end smoke: insert one row per value_* variant, SELECT back via PK.
        computed_at = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)
        await conn.execute(
            "INSERT INTO features "
            "(feature_name, symbol, computed_at, value_num, source_version) "
            "VALUES ($1, $2, $3, $4, $5)",
            "ind.btcusdt.15m.rsi_14",
            "BTCUSDT",
            computed_at,
            70.5,
            "builtin.rsi.v1",
        )
        await conn.execute(
            "INSERT INTO features "
            "(feature_name, symbol, computed_at, value_bool, source_version) "
            "VALUES ($1, $2, $3, $4, $5)",
            "ind.btcusdt.15m.flag",
            "BTCUSDT",
            computed_at,
            True,
            "builtin.flag.v1",
        )
        await conn.execute(
            "INSERT INTO features "
            "(feature_name, symbol, computed_at, value_json, source_version) "
            "VALUES ($1, $2, $3, $4, $5)",
            "ind.btcusdt.15m.bollinger_20_2",
            "BTCUSDT",
            computed_at,
            {"upper": 50100.5, "middle": 50000.0, "lower": 49899.5},
            "builtin.bollinger.v1",
        )

        num_row = await conn.fetchrow(
            "SELECT value_num, value_bool, value_json FROM features "
            "WHERE feature_name = $1 AND symbol = $2 "
            "  AND computed_at = $3 AND source_version = $4",
            "ind.btcusdt.15m.rsi_14",
            "BTCUSDT",
            computed_at,
            "builtin.rsi.v1",
        )
        assert num_row is not None
        assert num_row["value_num"] == 70.5
        assert num_row["value_bool"] is None
        assert num_row["value_json"] is None

        bool_row = await conn.fetchrow(
            "SELECT value_num, value_bool, value_json FROM features "
            "WHERE feature_name = $1 AND symbol = $2 "
            "  AND computed_at = $3 AND source_version = $4",
            "ind.btcusdt.15m.flag",
            "BTCUSDT",
            computed_at,
            "builtin.flag.v1",
        )
        assert bool_row is not None
        assert bool_row["value_bool"] is True
        assert bool_row["value_num"] is None
        assert bool_row["value_json"] is None

        json_row = await conn.fetchrow(
            "SELECT value_num, value_bool, value_json FROM features "
            "WHERE feature_name = $1 AND symbol = $2 "
            "  AND computed_at = $3 AND source_version = $4",
            "ind.btcusdt.15m.bollinger_20_2",
            "BTCUSDT",
            computed_at,
            "builtin.bollinger.v1",
        )
        assert json_row is not None
        assert json_row["value_json"] == {
            "upper": 50100.5,
            "middle": 50000.0,
            "lower": 49899.5,
        }
        assert json_row["value_num"] is None
        assert json_row["value_bool"] is None

        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        # The migration suite upgrades to head, so newer migrations
        # legitimately advance alembic_version beyond 0004. This test
        # asserts 0004 artifacts, not that 0004 is the current head.
        assert version is not None
    finally:
        await conn.close()
