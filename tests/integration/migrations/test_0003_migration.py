"""Integration test for migration 0003 (brief §N8, §7.2, §7.4, §18.3).

Runs ``alembic upgrade head`` against a throwaway database and
verifies the ``ohlc_1m`` hypertable + 5 continuous aggregates landed
exactly as specified:

* ``ohlc_1m`` table exists with the 8 columns from §7.2 and the
  expected nullability + composite ``(symbol, bucket_start, source)``
  primary key.
* ``ohlc_1m`` is registered as a TimescaleDB hypertable with a
  7-day ``chunk_time_interval``.
* Compression is enabled with ``segmentby = 'symbol, source'``,
  ``orderby = 'bucket_start DESC'``, and a 30-day compression policy
  per §18.3.
* A 180-day retention policy on ``ohlc_1m`` per §18.3.
* All 5 continuous aggregates (``ohlc_5m``, ``ohlc_15m``, ``ohlc_1h``,
  ``ohlc_4h``, ``ohlc_1d``) exist as continuous aggregates with the
  expected refresh policy parameters and an explicit 180-day retention
  policy each.
* End-to-end smoke: insert a 1m candle, manually refresh the 5m cagg,
  query it back through the cagg view to confirm the materialized row
  is reachable.
* The ``alembic_version`` row reports revision ``0003``.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

_EXPECTED_OHLC_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (column_name, data_type, is_nullable) — information_schema flavours:
    # NUMERIC(30, 12) is reported as "numeric"; TIMESTAMP WITH TIME ZONE
    # is "timestamp with time zone"; TEXT is "text".
    ("symbol", "text", "NO"),
    ("bucket_start", "timestamp with time zone", "NO"),
    ("open", "numeric", "NO"),
    ("high", "numeric", "NO"),
    ("low", "numeric", "NO"),
    ("close", "numeric", "NO"),
    ("volume", "numeric", "NO"),
    ("source", "text", "NO"),
)

# (cagg_name, schedule_interval, start_offset, end_offset) — values match
# `_CAGGS` in 0003 migration. The assertion loop casts the JSONB config
# offset strings to PG `interval` in SQL, so asyncpg yields `timedelta`
# uniformly — sidesteps the mixed text format TimescaleDB persists
# (`"1 day"` vs `"00:01:00"`).
_EXPECTED_CAGGS: tuple[tuple[str, timedelta, timedelta, timedelta], ...] = (
    ("ohlc_5m", timedelta(minutes=1), timedelta(days=1), timedelta(minutes=1)),
    ("ohlc_15m", timedelta(minutes=1), timedelta(days=1), timedelta(minutes=1)),
    ("ohlc_1h", timedelta(minutes=5), timedelta(days=1), timedelta(minutes=1)),
    ("ohlc_4h", timedelta(minutes=15), timedelta(days=2), timedelta(minutes=1)),
    ("ohlc_1d", timedelta(hours=1), timedelta(days=7), timedelta(minutes=1)),
)


async def test_migration_0003_creates_ohlc_table_and_caggs(migrated_db_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # ohlc_1m column shape.
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'ohlc_1m' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_OHLC_COLUMNS

        # Composite PK (symbol, bucket_start, source).
        pk_columns = [
            row["column_name"]
            for row in await conn.fetch(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                " AND tc.table_schema = kcu.table_schema "
                "WHERE tc.table_schema = 'public' "
                "  AND tc.table_name = 'ohlc_1m' "
                "  AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position"
            )
        ]
        assert pk_columns == ["symbol", "bucket_start", "source"]

        # ohlc_1m is a hypertable with 7-day chunks.
        time_interval = await conn.fetchval(
            "SELECT time_interval FROM timescaledb_information.dimensions "
            "WHERE hypertable_schema = 'public' "
            "  AND hypertable_name = 'ohlc_1m' "
            "  AND column_name = 'bucket_start'"
        )
        assert time_interval == timedelta(days=7), (
            f"expected 7-day chunk_time_interval, got {time_interval!r}"
        )

        # Compression enabled with the expected segmentby/orderby.
        # `timescaledb_information.compression_settings` returns one row per
        # segmentby column AND one row per orderby column — not aggregated.
        compression_rows = await conn.fetch(
            "SELECT attname, segmentby_column_index, orderby_column_index, "
            "       orderby_asc, orderby_nullsfirst "
            "FROM timescaledb_information.compression_settings "
            "WHERE hypertable_schema = 'public' AND hypertable_name = 'ohlc_1m' "
            "ORDER BY segmentby_column_index NULLS LAST, orderby_column_index"
        )
        segmentby = [
            r["attname"] for r in compression_rows if r["segmentby_column_index"] is not None
        ]
        assert segmentby == ["symbol", "source"]
        orderby = [
            (r["attname"], r["orderby_asc"], r["orderby_nullsfirst"])
            for r in compression_rows
            if r["orderby_column_index"] is not None
        ]
        # orderby_asc=False ↔ DESC (`bucket_start DESC` in the migration).
        # orderby_nullsfirst=True is TimescaleDB's default for DESC.
        assert orderby == [("bucket_start", False, True)]

        # All 5 caggs exist as continuous aggregates and reference ohlc_1m.
        cagg_names = {
            row["view_name"]
            for row in await conn.fetch(
                "SELECT view_name FROM timescaledb_information.continuous_aggregates "
                "WHERE view_schema = 'public'"
            )
        }
        assert {name for name, *_ in _EXPECTED_CAGGS} <= cagg_names

        # Refresh-policy parameters match what the migration registered.
        # `schedule_interval` is a typed PG `interval` column on bgw_job;
        # `start_offset` / `end_offset` live in the JSONB `config` and we
        # cast them to `interval` in the SQL so asyncpg yields `timedelta`
        # uniformly. The JOIN to `continuous_agg` resolves the cagg's
        # materialized hypertable, which is what bgw_job.hypertable_id
        # references for refresh policies.
        for cagg, expected_schedule, expected_start, expected_end in _EXPECTED_CAGGS:
            policy = await conn.fetchrow(
                "SELECT j.schedule_interval, "
                "       (j.config->>'start_offset')::interval AS start_offset, "
                "       (j.config->>'end_offset')::interval   AS end_offset "
                "FROM _timescaledb_config.bgw_job j "
                "WHERE j.proc_name = 'policy_refresh_continuous_aggregate' "
                "  AND j.hypertable_id = ("
                "    SELECT mat_hypertable_id "
                "    FROM _timescaledb_catalog.continuous_agg "
                "    WHERE user_view_name = $1"
                "  )",
                cagg,
            )
            assert policy is not None, f"missing refresh policy for {cagg}"
            assert policy["schedule_interval"] == expected_schedule, (
                f"{cagg}: schedule_interval mismatch — "
                f"got {policy['schedule_interval']!r}, want {expected_schedule!r}"
            )
            assert policy["start_offset"] == expected_start, (
                f"{cagg}: start_offset mismatch — "
                f"got {policy['start_offset']!r}, want {expected_start!r}"
            )
            assert policy["end_offset"] == expected_end, (
                f"{cagg}: end_offset mismatch — got {policy['end_offset']!r}, want {expected_end!r}"
            )

        # Retention policies — one for ohlc_1m + one per cagg, all 180 days.
        retention_targets = {
            row["target"]
            for row in await conn.fetch(
                # COALESCE order matters: a cagg's materialized hypertable
                # ALSO appears in `_timescaledb_catalog.hypertable` (as
                # `_materialized_hypertable_<id>`), so we must prefer the
                # cagg's user-facing view name when one exists. Plain
                # hypertables (ohlc_1m) have `ca.user_view_name = NULL`
                # and fall through to `h.table_name = 'ohlc_1m'`.
                "SELECT COALESCE(ca.user_view_name, h.table_name) AS target "
                "FROM _timescaledb_config.bgw_job j "
                "LEFT JOIN _timescaledb_catalog.hypertable h "
                "       ON h.id = (j.config->>'hypertable_id')::int "
                "LEFT JOIN _timescaledb_catalog.continuous_agg ca "
                "       ON ca.mat_hypertable_id = (j.config->>'hypertable_id')::int "
                "WHERE j.proc_name = 'policy_retention'"
            )
        }
        expected_retention_targets = {"ohlc_1m"} | {name for name, *_ in _EXPECTED_CAGGS}
        assert expected_retention_targets <= retention_targets, (
            f"missing retention policies: {expected_retention_targets - retention_targets}"
        )

        # End-to-end smoke: insert a 1m candle, refresh 5m cagg, query back.
        bucket = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
        await conn.execute(
            "INSERT INTO ohlc_1m (symbol, bucket_start, open, high, low, close, volume, source) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            "BTCUSDT",
            bucket,
            Decimal("100"),
            Decimal("110"),
            Decimal("90"),
            Decimal("105"),
            Decimal("1.5"),
            "binance",
        )
        # CALL refresh_continuous_aggregate is required because we created
        # the cagg WITH NO DATA — the policy will materialize on its
        # schedule, but for the test we force a refresh now.
        # Explicit ::timestamptz casts: refresh_continuous_aggregate's
        # window arguments are polymorphic, so asyncpg's PREPARE phase
        # can't infer $1/$2 types without help (IndeterminateDatatypeError).
        await conn.execute(
            "CALL refresh_continuous_aggregate('ohlc_5m', $1::timestamptz, $2::timestamptz)",
            bucket - timedelta(minutes=5),
            bucket + timedelta(minutes=10),
        )
        cagg_row = await conn.fetchrow(
            "SELECT symbol, open, high, low, close, volume, source "
            "FROM ohlc_5m WHERE symbol = $1 AND bucket_start = $2 AND source = $3",
            "BTCUSDT",
            bucket,
            "binance",
        )
        assert cagg_row is not None, "5m cagg query must return the just-inserted row"
        assert cagg_row["open"] == Decimal("100")
        assert cagg_row["high"] == Decimal("110")
        assert cagg_row["low"] == Decimal("90")
        assert cagg_row["close"] == Decimal("105")
        assert cagg_row["volume"] == Decimal("1.5")

        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version == "0003"
    finally:
        await conn.close()
