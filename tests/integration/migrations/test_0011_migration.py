"""Integration test for migration 0011 (T-401a — brief §7.2:1108-1126, §16.8:2261-2264).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the ``audit_events`` hypertable landed exactly as specified.

Schema lock-site per §7.2:1110-1122 verbatim:

* 10 columns: ``id BIGSERIAL`` + ``occurred_at TIMESTAMPTZ`` +
  ``actor TEXT`` + ``action TEXT`` + ``entity_type TEXT`` +
  ``entity_id TEXT`` + ``before_state JSONB nullable`` +
  ``after_state JSONB nullable`` + ``correlation_id TEXT nullable`` +
  ``meta JSONB DEFAULT '{}'``.
* Composite PK ``(occurred_at, id)`` per TimescaleDB hypertable convention.
* Hypertable on ``occurred_at`` with **30-day** chunks per §7.2:1124
  (audit events are sparser than 7-day signals/features).
* Index ``ae_entity (entity_type, entity_id, occurred_at DESC)``
  verbatim per §7.2:1125 — load-bearing for T-405 reader.
* No FK on any column (audit-stream stands alone).
* No UNIQUE constraint at DB layer (idempotency is application concern).
* ``meta`` server default ``'{}'::jsonb`` applied on INSERT without
  explicit meta column (L-008 active control: SQL literal NOT Python ``{}``).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg

_EXPECTED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("occurred_at", "timestamp with time zone", "NO"),
    ("actor", "text", "NO"),
    ("action", "text", "NO"),
    ("entity_type", "text", "NO"),
    ("entity_id", "text", "NO"),
    ("before_state", "jsonb", "YES"),
    ("after_state", "jsonb", "YES"),
    ("correlation_id", "text", "YES"),
    ("meta", "jsonb", "NO"),
)

_T_EVT_1 = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
_T_EVT_2 = datetime(2026, 5, 3, 12, 0, 1, tzinfo=UTC)


async def test_migration_0011_creates_audit_events_hypertable(
    migrated_db_dsn: str,
) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # (a) Column shape verbatim per §7.2:1110-1122.
        # 3 nullable columns: before_state, after_state, correlation_id.
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'audit_events' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_COLUMNS

        # (b) Composite PK (occurred_at, id).
        pk_columns = [
            row["column_name"]
            for row in await conn.fetch(
                """
                SELECT a.attname AS column_name
                FROM pg_constraint c
                JOIN pg_attribute a
                  ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                WHERE c.contype = 'p'
                  AND c.conrelid = 'public.audit_events'::regclass
                ORDER BY array_position(c.conkey, a.attnum)
                """
            )
        ]
        assert pk_columns == ["occurred_at", "id"]

        # (c) No UNIQUE constraints (idempotency is application concern).
        unique_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM pg_constraint
            WHERE contype = 'u'
              AND conrelid = 'public.audit_events'::regclass
            """
        )
        assert unique_count == 0

        # (d) Hypertable + 30-day chunk_time_interval per §7.2:1124
        #     (NOT 7-day like signals/features; audit events are sparser).
        hypertable_row = await conn.fetchrow(
            """
            SELECT h.table_name, d.interval_length
            FROM _timescaledb_catalog.hypertable h
            JOIN _timescaledb_catalog.dimension d ON d.hypertable_id = h.id
            WHERE h.table_name = 'audit_events'
            """
        )
        assert hypertable_row is not None
        # interval_length is microseconds; 30 days = 30 * 86400 * 1_000_000.
        assert hypertable_row["interval_length"] == 30 * 86400 * 1_000_000

        # (e) Index ae_entity (entity_type, entity_id, occurred_at DESC) verbatim.
        ae_entity_def = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' "
            "AND tablename = 'audit_events' "
            "AND indexname = 'ae_entity'"
        )
        assert ae_entity_def is not None
        assert "entity_type" in ae_entity_def
        assert "entity_id" in ae_entity_def
        assert "occurred_at" in ae_entity_def
        # DESC ordering for top-K read pattern (T-405 audit log viewer).
        assert "DESC" in ae_entity_def

        # (f) `meta` server default '{}'::jsonb applied on INSERT without explicit meta.
        await conn.execute(
            """
            INSERT INTO audit_events (
                occurred_at, actor, action, entity_type, entity_id,
                before_state, after_state, correlation_id
            )
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)
            """,
            _T_EVT_1,
            "lan:127.0.0.1",
            "symbol_map.create",
            "symbol_map",
            "BTCUSDT.P",
            None,
            json.dumps({"input_symbol": "BTCUSDT.P", "canonical_symbol": "BTCUSDT"}),
            "cid-create-1",
        )
        meta_row = await conn.fetchrow(
            "SELECT meta FROM audit_events "
            "WHERE entity_type = $1 AND entity_id = $2 AND occurred_at = $3",
            "symbol_map",
            "BTCUSDT.P",
            _T_EVT_1,
        )
        assert meta_row is not None
        assert json.loads(meta_row["meta"]) == {}

        # (g) Nullable columns accept NULL at DB layer.
        await conn.execute(
            """
            INSERT INTO audit_events (
                occurred_at, actor, action, entity_type, entity_id,
                before_state, after_state, correlation_id
            )
            VALUES ($1, $2, $3, $4, $5, NULL, NULL, NULL)
            """,
            _T_EVT_2,
            "system",
            "test.no_state_change",
            "test_entity",
            "id-2",
        )
        null_row = await conn.fetchrow(
            "SELECT before_state, after_state, correlation_id "
            "FROM audit_events "
            "WHERE entity_type = $1 AND entity_id = $2 AND occurred_at = $3",
            "test_entity",
            "id-2",
            _T_EVT_2,
        )
        assert null_row is not None
        assert null_row["before_state"] is None
        assert null_row["after_state"] is None
        assert null_row["correlation_id"] is None

        # (h) No foreign keys (audit-stream stands alone).
        fk_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM pg_constraint
            WHERE contype = 'f'
              AND conrelid = 'public.audit_events'::regclass
            """
        )
        assert fk_count == 0
    finally:
        await conn.close()
