"""Integration test for migration 0007 (brief §N8, §7.2, §N1, §18.3).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the ``trading_events`` hypertable landed exactly as specified.

Two test functions:

* ``test_migration_0007_creates_trading_events_hypertable`` — full schema
  + smoke E2E + composite-PK uniqueness.
* ``test_trading_events_hypertable_retention_365d_no_compression`` —
  combined positive retention + negative compression assertions; single
  grep target for future readers (mirror grep-friendly naming locked at
  Gate-1 plan-reviewer Write-time guidance #3).

Schema verbatim per §7.2 lines 1091-1106:

* 6 columns: ``id BIGSERIAL``, ``occurred_at TIMESTAMPTZ NOT NULL``,
  ``bot_id TEXT`` (nullable), ``correlation_id TEXT`` (nullable),
  ``event_type TEXT NOT NULL``, ``payload JSONB NOT NULL``.
* Composite PK ``(occurred_at, id)``.
* 2 indexes ``te_bot_type (bot_id, event_type, occurred_at DESC)``
  + ``te_correlation (correlation_id)``.
* Hypertable on ``occurred_at`` with 7-day chunks.
* Retention 365 days per §18.3 line 1176 + 2438.
* NO compression policy per §18.3 lines 1178-1183 (compression for
  ``ohlc_1m`` + ``features`` only).
* No FK on any column (append-only audit stream; events outlive source rows).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

_EXPECTED_TRADING_EVENTS_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("occurred_at", "timestamp with time zone", "NO"),
    ("bot_id", "text", "YES"),
    ("correlation_id", "text", "YES"),
    ("event_type", "text", "NO"),
    ("payload", "jsonb", "NO"),
)


async def test_migration_0007_creates_trading_events_hypertable(
    migrated_db_dsn: str,
) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # JSONB codec for payload round-trip.
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

        # (a) Column shape per §7.2 verbatim.
        columns = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in await conn.fetch(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'trading_events' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_TRADING_EVENTS_COLUMNS

        # (b) Composite PK (occurred_at, id).
        pk_columns = [
            row["column_name"]
            for row in await conn.fetch(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                " AND tc.table_schema = kcu.table_schema "
                "WHERE tc.table_schema = 'public' "
                "  AND tc.table_name = 'trading_events' "
                "  AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position"
            )
        ]
        assert pk_columns == ["occurred_at", "id"]

        # (c) Anti-FK on trading_events.bot_id per §7.2 verbatim — mirror
        # T-202 W#2 / T-203 W#4 precedent. Append-only audit stream
        # outlives source rows; no FK on bots(bot_id). correlation_id has
        # no `correlations` parent table; no symmetry-add risk; not asserted
        # per OQ-2.
        bot_id_fk_count = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_constraint c "
            "JOIN pg_class t ON c.conrelid = t.oid "
            "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey) "
            "WHERE t.relname = 'trading_events' "
            "  AND c.contype = 'f' "
            "  AND a.attname = 'bot_id'"
        )
        assert bot_id_fk_count == 0, (
            "trading_events.bot_id must have NO FK on bots(bot_id) per §7.2 verbatim"
        )

        # (d) Server defaults — occurred_at IS NULL per §N1 invariant
        # (no CURRENT_TIMESTAMP / NOW() in SQL); no other column has
        # server_default per brief.
        defaults = {
            row["column_name"]: row["column_default"]
            for row in await conn.fetch(
                "SELECT column_name, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'trading_events'"
            )
        }
        assert defaults["occurred_at"] is None
        assert defaults["bot_id"] is None
        assert defaults["correlation_id"] is None
        assert defaults["event_type"] is None
        assert defaults["payload"] is None

        # (e) Indexes — 2 per §7.2 verbatim plus the implicit PK index.
        # Use subset assertion for vendor-metadata robustness per W#1
        # (instead of strict count) — surface diff in error message
        # if TimescaleDB ever emits an extra catalog entry.
        index_defs = {
            row["indexname"]: row["indexdef"]
            for row in await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'trading_events'"
            )
        }
        expected_indexes = {"trading_events_pkey", "te_bot_type", "te_correlation"}
        actual_indexes = set(index_defs)
        assert expected_indexes <= actual_indexes, (
            f"missing indexes: {expected_indexes - actual_indexes}; "
            f"unexpected indexes: {actual_indexes - expected_indexes}"
        )
        assert "(bot_id, event_type, occurred_at DESC)" in index_defs["te_bot_type"]
        assert "(correlation_id)" in index_defs["te_correlation"]

        # (f) Hypertable with 7-day chunks on occurred_at.
        time_interval = await conn.fetchval(
            "SELECT time_interval FROM timescaledb_information.dimensions "
            "WHERE hypertable_schema = 'public' "
            "  AND hypertable_name = 'trading_events' "
            "  AND column_name = 'occurred_at'"
        )
        assert time_interval == timedelta(days=7), (
            f"expected 7-day chunk_time_interval, got {time_interval!r}"
        )

        # (h) Smoke E2E — 3 nullability variants per OQ-3 default A.
        # Each variant carries an explicit comment per W#4 so the contract
        # is legible for future emitters (T-216b/T-218/T-219/T-220/T-221).
        bot_id = f"test_t204_{uuid.uuid4().hex[:8]}"

        # h1 — typical case: both bot_id + correlation_id populated.
        # T-216b emit path during order placement carries both.
        h1_occurred_at = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
        h1_payload = {
            "order_id": 1,
            "exchange_order_id": "abc-123",
            "symbol": "BTCUSDT",
        }
        await conn.execute(
            "INSERT INTO trading_events "
            "(occurred_at, bot_id, correlation_id, event_type, payload) "
            "VALUES ($1, $2, $3, $4, $5)",
            h1_occurred_at,
            bot_id,
            "corr-t204-h1",
            "order_placed",
            h1_payload,
        )
        h1_row = await conn.fetchrow(
            "SELECT bot_id, correlation_id, event_type, payload "
            "FROM trading_events WHERE occurred_at = $1 AND bot_id = $2",
            h1_occurred_at,
            bot_id,
        )
        assert h1_row is not None
        assert h1_row["bot_id"] == bot_id
        assert h1_row["correlation_id"] == "corr-t204-h1"
        assert h1_row["event_type"] == "order_placed"
        assert h1_row["payload"] == h1_payload

        # h2 — system-level event, no request correlation. Reconciliation
        # startup, audit cron tick, etc. emit with bot_id populated but
        # correlation_id NULL (T-220 audit cron tick precedent).
        h2_occurred_at = datetime(2026, 4, 27, 12, 0, 1, tzinfo=UTC)
        h2_payload = {"audit_run": "5min", "delta": "0.50"}
        await conn.execute(
            "INSERT INTO trading_events "
            "(occurred_at, bot_id, correlation_id, event_type, payload) "
            "VALUES ($1, $2, $3, $4, $5)",
            h2_occurred_at,
            bot_id,
            None,
            "reconcile_adjust",
            h2_payload,
        )
        h2_row = await conn.fetchrow(
            "SELECT bot_id, correlation_id, event_type, payload "
            "FROM trading_events WHERE occurred_at = $1 AND bot_id = $2",
            h2_occurred_at,
            bot_id,
        )
        assert h2_row is not None
        assert h2_row["bot_id"] == bot_id
        assert h2_row["correlation_id"] is None
        assert h2_row["event_type"] == "reconcile_adjust"
        assert h2_row["payload"] == h2_payload

        # h3 — infrastructure event pre-bot-context: both bot_id +
        # correlation_id NULL. Service startup before adapter pool
        # composes; T-221 fleet-level reconciliation startup logs.
        h3_occurred_at = datetime(2026, 4, 27, 12, 0, 2, tzinfo=UTC)
        h3_payload = {"event": "execution_service_startup", "version": "v0.1"}
        await conn.execute(
            "INSERT INTO trading_events "
            "(occurred_at, bot_id, correlation_id, event_type, payload) "
            "VALUES ($1, $2, $3, $4, $5)",
            h3_occurred_at,
            None,
            None,
            "system_startup",
            h3_payload,
        )
        h3_row = await conn.fetchrow(
            "SELECT bot_id, correlation_id, event_type, payload "
            "FROM trading_events "
            "WHERE occurred_at = $1 AND event_type = $2",
            h3_occurred_at,
            "system_startup",
        )
        assert h3_row is not None
        assert h3_row["bot_id"] is None
        assert h3_row["correlation_id"] is None
        assert h3_row["event_type"] == "system_startup"
        assert h3_row["payload"] == h3_payload

        # (j) Composite-PK uniqueness via approach (a) — mirror T-203
        # OQ-10 / W#5: capture (occurred_at, id) from a fresh INSERT,
        # then re-insert with same composite key → UniqueViolationError.
        captured = await conn.fetchrow(
            "INSERT INTO trading_events "
            "(occurred_at, bot_id, correlation_id, event_type, payload) "
            "VALUES ($1, $2, $3, $4, $5) "
            "RETURNING occurred_at, id",
            datetime(2026, 4, 27, 12, 0, 3, tzinfo=UTC),
            bot_id,
            None,
            "fill",
            {"qty": "0.5"},
        )
        assert captured is not None
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(
                "INSERT INTO trading_events "
                "(occurred_at, id, bot_id, correlation_id, event_type, payload) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                captured["occurred_at"],
                captured["id"],
                bot_id,
                None,
                "fill",
                {"qty": "0.5"},
            )

        # (i) alembic_version exists (permissive — successor migrations
        # may advance head).
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version is not None
    finally:
        await conn.close()


async def test_trading_events_hypertable_retention_365d_no_compression(
    migrated_db_dsn: str,
) -> None:
    """Combined retention-positive + compression-negative invariants on
    ``trading_events``.

    Single grep target locks both regression directions at once:

    * **Positive**: retention policy 365 days IS present per §18.3 line
      1176 + line 2438. Catches accidental drop of retention (would lead
      to forever-accumulating audit table).
    * **Negative**: NO compression policy IS present per §18.3 lines
      1178-1183 (compression for ``ohlc_1m`` + ``features`` only).
      Catches accidental copy-paste from 0003/0004 retention+compression
      block (would add storage bloat with marginal benefit + decompress
      overhead during audit replay).
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        policy_rows = await conn.fetch(
            "SELECT j.proc_name, j.config "
            "FROM _timescaledb_config.bgw_job j "
            "JOIN _timescaledb_catalog.hypertable h "
            "  ON h.id = (j.config->>'hypertable_id')::int "
            "WHERE h.table_name = 'trading_events'"
        )
        proc_names = {row["proc_name"] for row in policy_rows}

        # Positive — retention policy IS present.
        assert "policy_retention" in proc_names, (
            "trading_events must have policy_retention per §18.3 line 1176 + 2438"
        )

        # Drop_after = 365 days. parse the bgw_job.config json.
        retention_row = next(row for row in policy_rows if row["proc_name"] == "policy_retention")
        retention_config = json.loads(retention_row["config"])
        drop_after = retention_config["drop_after"]
        # PostgreSQL serialises INTERVAL '365 days' as the string "365 days".
        assert "365" in drop_after, f"expected retention drop_after = 365 days, got {drop_after!r}"
        assert "day" in drop_after.lower(), (
            f"expected retention drop_after to mention days, got {drop_after!r}"
        )

        # Negative — NO compression policy.
        assert "policy_compression" not in proc_names, (
            "trading_events must NOT have policy_compression per §18.3 lines 1178-1183 "
            "(compression specified for ohlc_1m + features only)"
        )
    finally:
        await conn.close()
