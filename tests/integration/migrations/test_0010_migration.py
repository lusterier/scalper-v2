"""Integration test for migration 0010 (T-301 — brief §7.2:1036-1055, §9.4:1543).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the ``scoring_evaluations`` hypertable landed exactly as specified.

Schema lock-site per §7.2:1039-1050 verbatim:

* 11 columns: ``id BIGSERIAL`` + ``bot_id TEXT`` + ``signal_id BIGINT`` +
  ``evaluated_at TIMESTAMPTZ`` + ``trigger_threshold DOUBLE PRECISION`` +
  ``total_score DOUBLE PRECISION`` + ``decision TEXT`` +
  ``config_version INT`` + ``rule_results JSONB`` +
  ``feature_snapshot JSONB`` + ``correlation_id TEXT``.
* L-005 active control: ``trigger_threshold`` + ``total_score`` are
  ``double precision`` (NOT ``real``). Migration uses ``sa.Double()``.
* Composite PK ``(evaluated_at, id)`` per TimescaleDB hypertable convention.
* Hypertable on ``evaluated_at`` with 30-day chunks per §7.2:1053.
* Indexes ``se_bot_signal`` + ``se_decision`` per §7.2:1054-1055 (verbatim names).
* No FK on any column (audit-stream stands alone).
* No UNIQUE constraint at DB layer (T-310 dedup-ring concern).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg

_EXPECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("bot_id", "text", "NO"),
    ("signal_id", "bigint", "NO"),
    ("evaluated_at", "timestamp with time zone", "NO"),
    ("trigger_threshold", "double precision", "NO"),
    ("total_score", "double precision", "NO"),
    ("decision", "text", "NO"),
    ("config_version", "integer", "NO"),
    ("rule_results", "jsonb", "NO"),
    ("feature_snapshot", "jsonb", "NO"),
    ("correlation_id", "text", "NO"),
)

_T_EVAL_1 = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)
_T_EVAL_2 = datetime(2026, 5, 2, 12, 0, 1, tzinfo=UTC)


async def test_migration_0010_creates_scoring_evaluations_hypertable(
    migrated_db_dsn: str,
) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # (a) Column shape verbatim per §7.2:1039-1050.
        # L-005 active control pin: trigger_threshold + total_score = double precision.
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'scoring_evaluations' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_COLUMNS

        # (b) Composite PK (evaluated_at, id).
        pk_columns = [
            row["column_name"]
            for row in await conn.fetch(
                """
                SELECT a.attname AS column_name
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'p'
                  AND c.conrelid = 'public.scoring_evaluations'::regclass
                ORDER BY array_position(c.conkey, a.attnum)
                """
            )
        ]
        assert pk_columns == ["evaluated_at", "id"]

        # (c) No UNIQUE constraints (T-310 dedup-ring concern; not DB-enforced).
        unique_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM pg_constraint
            WHERE contype = 'u'
              AND conrelid = 'public.scoring_evaluations'::regclass
            """
        )
        assert unique_count == 0

        # (d) Hypertable + 30-day chunk_time_interval per §7.2:1053.
        hypertable_row = await conn.fetchrow(
            """
            SELECT h.table_name, d.interval_length
            FROM _timescaledb_catalog.hypertable h
            JOIN _timescaledb_catalog.dimension d ON d.hypertable_id = h.id
            WHERE h.table_name = 'scoring_evaluations'
            """
        )
        assert hypertable_row is not None
        # interval_length is microseconds; 30 days = 30 * 86400 * 1_000_000.
        assert hypertable_row["interval_length"] == 30 * 86400 * 1_000_000

        # (e) Indexes se_bot_signal + se_decision per §7.2:1054-1055 verbatim.
        bot_signal_def = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "AND tablename = 'scoring_evaluations' "
            "AND indexname = 'se_bot_signal'"
        )
        assert bot_signal_def is not None
        assert "bot_id" in bot_signal_def
        assert "signal_id" in bot_signal_def

        decision_def = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "AND tablename = 'scoring_evaluations' "
            "AND indexname = 'se_decision'"
        )
        assert decision_def is not None
        assert "decision" in decision_def
        assert "evaluated_at" in decision_def

        # (f) DOUBLE PRECISION 1.5 round-trip exact (L-005 pin).
        await conn.execute(
            """
            INSERT INTO scoring_evaluations (
                bot_id, signal_id, evaluated_at, trigger_threshold, total_score,
                decision, config_version, rule_results, feature_snapshot, correlation_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10)
            """,
            "alpha",
            42,
            _T_EVAL_1,
            1.5,
            2.5,
            "execute",
            1,
            json.dumps(
                [
                    {
                        "name": "r1",
                        "weight": 1.0,
                        "applied_weight": 1.0,
                        "result": "True",
                        "error": None,
                    }
                ]
            ),
            json.dumps({"sym1": {"value_num": "1.5"}}),
            "cid-1",
        )
        row = await conn.fetchrow(
            "SELECT trigger_threshold, total_score, rule_results, feature_snapshot "
            "FROM scoring_evaluations WHERE bot_id = $1 AND signal_id = $2",
            "alpha",
            42,
        )
        assert row is not None
        assert row["trigger_threshold"] == 1.5
        assert row["total_score"] == 2.5
        assert json.loads(row["rule_results"]) == [
            {"name": "r1", "weight": 1.0, "applied_weight": 1.0, "result": "True", "error": None}
        ]
        assert json.loads(row["feature_snapshot"]) == {"sym1": {"value_num": "1.5"}}

        # (g) decision TEXT accepts any string at DB layer (T-300 Literal is app-side only).
        await conn.execute(
            """
            INSERT INTO scoring_evaluations (
                bot_id, signal_id, evaluated_at, trigger_threshold, total_score,
                decision, config_version, rule_results, feature_snapshot, correlation_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10)
            """,
            "beta",
            43,
            _T_EVAL_2,
            1.0,
            0.0,
            "garbage_at_db_layer",
            1,
            json.dumps([]),
            json.dumps({}),
            "cid-2",
        )

        # (h) No foreign keys (audit-stream stands alone).
        fk_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM pg_constraint
            WHERE contype = 'f'
              AND conrelid = 'public.scoring_evaluations'::regclass
            """
        )
        assert fk_count == 0
    finally:
        await conn.close()
