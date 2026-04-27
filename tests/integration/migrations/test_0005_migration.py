"""Integration test for migration 0005 (brief §N8, §7.2, §18.3).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the orders + trades + executions FK chain landed exactly as specified:

* Three tables (``orders``, ``trades``, ``executions``) with the
  columns / nullability from §7.2 lines 956-1034 verbatim.
* Composite PK ``(executed_at, id)`` on ``executions``; simple PK
  ``id`` on ``orders`` and ``trades``.
* Five FKs: orders.bot_id → bots.bot_id, trades.bot_id → bots.bot_id,
  trades.open_order_id → orders.id, executions.order_id → orders.id,
  executions.trade_id → trades.id.
* Six indexes per §7.2: ``orders_bot_status``, ``orders_correlation``,
  ``trades_bot_status``, ``trades_closed_at`` (partial WHERE
  status='closed'), ``executions_exchange_id`` (UNIQUE),
  ``executions_trade``.
* ``executions`` is a TimescaleDB hypertable with 7-day chunks on
  ``executed_at``.
* **Anti-test**: ``executions`` has ZERO ``policy_retention`` and
  ZERO ``policy_compression`` rows (§18.3 line 2439 — trades +
  executions kept forever; copy-paste from 0003/0004 must not add
  policies). Anti-test is the negation of the test_0004 pattern.
* Smoke E2E: INSERT one row per table along the FK chain, SELECT
  back via PK, assert NUMERIC(30,12) precision preservation +
  JSONB codec round-trip + UTC timestamp round-trip.
* The ``alembic_version`` row exists (permissive — successor
  migrations legitimately advance head).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

_EXPECTED_ORDERS_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("bot_id", "text", "NO"),
    ("signal_id", "bigint", "YES"),
    ("correlation_id", "text", "NO"),
    ("exchange_order_id", "text", "YES"),
    ("exchange", "text", "NO"),
    ("symbol", "text", "NO"),
    ("side", "text", "NO"),
    ("order_type", "text", "NO"),
    ("qty", "numeric", "NO"),
    ("price", "numeric", "YES"),
    ("status", "text", "NO"),
    ("requested_at", "timestamp with time zone", "NO"),
    ("placed_at", "timestamp with time zone", "YES"),
    ("filled_at", "timestamp with time zone", "YES"),
    ("closed_at", "timestamp with time zone", "YES"),
    ("idempotent", "boolean", "NO"),
    ("meta", "jsonb", "NO"),
)

_EXPECTED_TRADES_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("bot_id", "text", "NO"),
    ("signal_id", "bigint", "YES"),
    ("open_order_id", "bigint", "NO"),
    ("close_order_id", "bigint", "YES"),
    ("symbol", "text", "NO"),
    ("side", "text", "NO"),
    ("entry_price", "numeric", "NO"),
    ("exit_price", "numeric", "YES"),
    ("qty", "numeric", "NO"),
    ("notional_usd", "numeric", "NO"),
    ("realized_pnl", "numeric", "YES"),
    ("fees_paid", "numeric", "YES"),
    ("close_reason", "text", "YES"),
    ("opened_at", "timestamp with time zone", "NO"),
    ("closed_at", "timestamp with time zone", "YES"),
    ("status", "text", "NO"),
    ("mfe_pct", "double precision", "YES"),
    ("mae_pct", "double precision", "YES"),
    ("confidence_score", "double precision", "YES"),
    ("meta", "jsonb", "NO"),
)

_EXPECTED_EXECUTIONS_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("id", "bigint", "NO"),
    ("exchange_exec_id", "text", "NO"),
    ("order_id", "bigint", "NO"),
    ("trade_id", "bigint", "YES"),
    ("bot_id", "text", "NO"),
    ("symbol", "text", "NO"),
    ("side", "text", "NO"),
    ("price", "numeric", "NO"),
    ("qty", "numeric", "NO"),
    ("fee", "numeric", "NO"),
    ("exec_type", "text", "NO"),
    ("executed_at", "timestamp with time zone", "NO"),
)


async def _columns_of(conn: asyncpg.Connection, table_name: str) -> list[tuple[str, str, str]]:
    rows = await conn.fetch(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 "
        "ORDER BY ordinal_position",
        table_name,
    )
    return [(row["column_name"], row["data_type"], row["is_nullable"]) for row in rows]


async def _pk_columns_of(conn: asyncpg.Connection, table_name: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        " AND tc.table_schema = kcu.table_schema "
        "WHERE tc.table_schema = 'public' "
        "  AND tc.table_name = $1 "
        "  AND tc.constraint_type = 'PRIMARY KEY' "
        "ORDER BY kcu.ordinal_position",
        table_name,
    )
    return [row["column_name"] for row in rows]


async def test_migration_0005_creates_orders_trades_executions_chain(
    migrated_db_dsn: str,
) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # asyncpg defaults to raw JSON string for jsonb; register a codec
        # so the smoke INSERT/SELECT round-trips meta as Python dict.
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

        # (a) Column shapes — three tables verbatim per §7.2.
        assert tuple(await _columns_of(conn, "orders")) == _EXPECTED_ORDERS_COLUMNS
        assert tuple(await _columns_of(conn, "trades")) == _EXPECTED_TRADES_COLUMNS
        assert tuple(await _columns_of(conn, "executions")) == _EXPECTED_EXECUTIONS_COLUMNS

        # (b) PKs — simple `id` on orders + trades; composite (executed_at, id) on executions.
        assert await _pk_columns_of(conn, "orders") == ["id"]
        assert await _pk_columns_of(conn, "trades") == ["id"]
        assert await _pk_columns_of(conn, "executions") == ["executed_at", "id"]

        # (c) FK chain — five edges. Use information_schema to enumerate.
        fk_rows = await conn.fetch(
            "SELECT tc.table_name AS child_table, kcu.column_name AS child_column, "
            "       ccu.table_name AS parent_table, ccu.column_name AS parent_column "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "  ON tc.constraint_name = kcu.constraint_name "
            " AND tc.table_schema = kcu.table_schema "
            "JOIN information_schema.constraint_column_usage ccu "
            "  ON tc.constraint_name = ccu.constraint_name "
            " AND tc.table_schema = ccu.table_schema "
            "WHERE tc.table_schema = 'public' "
            "  AND tc.constraint_type = 'FOREIGN KEY' "
            "  AND tc.table_name IN ('orders', 'trades', 'executions') "
            "ORDER BY tc.table_name, kcu.column_name"
        )
        fks = {
            (row["child_table"], row["child_column"], row["parent_table"], row["parent_column"])
            for row in fk_rows
        }
        assert ("orders", "bot_id", "bots", "bot_id") in fks
        assert ("trades", "bot_id", "bots", "bot_id") in fks
        assert ("trades", "open_order_id", "orders", "id") in fks
        assert ("executions", "order_id", "orders", "id") in fks
        assert ("executions", "trade_id", "trades", "id") in fks
        # Anti-FK assertions per §7.2 verbatim — close_order_id on trades and
        # bot_id on executions are deliberately NOT FKs.
        assert not any(child == "executions" and column == "bot_id" for child, column, _, _ in fks)
        assert not any(
            child == "trades" and column == "close_order_id" for child, column, _, _ in fks
        )

        # (d) Indexes — six per §7.2 verbatim.
        index_defs = {
            row["indexname"]: row["indexdef"]
            for row in await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "  AND tablename IN ('orders', 'trades', 'executions')"
            )
        }
        assert "orders_bot_status" in index_defs
        assert "(bot_id, status)" in index_defs["orders_bot_status"]
        assert "orders_correlation" in index_defs
        assert "(correlation_id)" in index_defs["orders_correlation"]
        assert "trades_bot_status" in index_defs
        assert "(bot_id, status)" in index_defs["trades_bot_status"]
        assert "trades_closed_at" in index_defs
        assert "(closed_at DESC)" in index_defs["trades_closed_at"]
        assert "WHERE (status = 'closed'::text)" in index_defs["trades_closed_at"]
        assert "executions_exchange_id" in index_defs
        assert "UNIQUE" in index_defs["executions_exchange_id"]
        assert "(exchange_exec_id, executed_at)" in index_defs["executions_exchange_id"]
        assert "executions_trade" in index_defs
        assert "(trade_id)" in index_defs["executions_trade"]

        # (e) executions is a hypertable with 7-day chunks on executed_at.
        time_interval = await conn.fetchval(
            "SELECT time_interval FROM timescaledb_information.dimensions "
            "WHERE hypertable_schema = 'public' "
            "  AND hypertable_name = 'executions' "
            "  AND column_name = 'executed_at'"
        )
        assert time_interval == timedelta(days=7), (
            f"expected 7-day chunk_time_interval, got {time_interval!r}"
        )

        # (f) Anti-test: NO retention/compression policies on executions.
        # §18.3 line 2439 — trades + executions kept forever. This is the
        # NEGATION of the test_0004 pattern (which asserts presence on
        # features). Copy-paste from 0003/0004 must not add policies.
        policy_targets = {
            (row["proc_name"], row["table_name"])
            for row in await conn.fetch(
                "SELECT j.proc_name, h.table_name "
                "FROM _timescaledb_config.bgw_job j "
                "JOIN _timescaledb_catalog.hypertable h "
                "  ON h.id = (j.config->>'hypertable_id')::int "
                "WHERE h.table_name = 'executions'"
            )
        }
        assert ("policy_retention", "executions") not in policy_targets
        assert ("policy_compression", "executions") not in policy_targets

        # (g) Smoke E2E — insert along the FK chain, SELECT back via PK,
        # assert NUMERIC(30,12) precision preservation + JSONB round-trip
        # + UTC timestamp round-trip.
        bot_id = f"test_t202_{uuid.uuid4().hex[:8]}"
        await conn.execute(
            "INSERT INTO bots "
            "(bot_id, display_name, created_at, status, exchange_mode, "
            " config_hash, config_applied_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            bot_id,
            "T-202 smoke",
            datetime(2026, 4, 27, tzinfo=UTC),
            "active",
            "paper",
            "sha256:smoke",
            datetime(2026, 4, 27, tzinfo=UTC),
        )

        max_precision_qty = Decimal("0.123456789012")
        max_precision_price = Decimal("65000.123456789012")

        order_id = await conn.fetchval(
            "INSERT INTO orders "
            "(bot_id, correlation_id, exchange, symbol, side, order_type, "
            " qty, price, status, requested_at, idempotent, meta) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
            "RETURNING id",
            bot_id,
            "corr-t202",
            "bybit",
            "BTCUSDT",
            "buy",
            "market",
            max_precision_qty,
            max_precision_price,
            "filled",
            datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
            False,
            {"source": "T-202", "attempt": 1},
        )
        assert isinstance(order_id, int)

        trade_id = await conn.fetchval(
            "INSERT INTO trades "
            "(bot_id, open_order_id, symbol, side, entry_price, qty, "
            " notional_usd, opened_at, status, meta) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
            "RETURNING id",
            bot_id,
            order_id,
            "BTCUSDT",
            "buy",
            max_precision_price,
            max_precision_qty,
            Decimal("8000.0000"),
            datetime(2026, 4, 27, 12, 0, 1, tzinfo=UTC),
            "open",
            {"open_attempt": 1},
        )
        assert isinstance(trade_id, int)

        executed_at = datetime(2026, 4, 27, 12, 0, 2, tzinfo=UTC)
        await conn.execute(
            "INSERT INTO executions "
            "(exchange_exec_id, order_id, trade_id, bot_id, symbol, side, "
            " price, qty, fee, exec_type, executed_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            "exec-t202-1",
            order_id,
            trade_id,
            bot_id,
            "BTCUSDT",
            "buy",
            max_precision_price,
            max_precision_qty,
            Decimal("0.00012345"),
            "open",
            executed_at,
        )

        # SELECT back — orders.
        order_row = await conn.fetchrow(
            "SELECT qty, price, requested_at, idempotent, meta FROM orders WHERE id = $1",
            order_id,
        )
        assert order_row is not None
        assert order_row["qty"] == max_precision_qty
        assert order_row["price"] == max_precision_price
        assert order_row["requested_at"] == datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
        assert order_row["idempotent"] is False
        assert order_row["meta"] == {"source": "T-202", "attempt": 1}

        # SELECT back — trades.
        trade_row = await conn.fetchrow(
            "SELECT entry_price, qty, notional_usd, status, meta FROM trades WHERE id = $1",
            trade_id,
        )
        assert trade_row is not None
        assert trade_row["entry_price"] == max_precision_price
        assert trade_row["qty"] == max_precision_qty
        assert trade_row["notional_usd"] == Decimal("8000.0000")
        assert trade_row["status"] == "open"
        assert trade_row["meta"] == {"open_attempt": 1}

        # SELECT back — executions (composite-PK lookup).
        exec_row = await conn.fetchrow(
            "SELECT exchange_exec_id, order_id, trade_id, price, qty, fee, exec_type "
            "FROM executions WHERE executed_at = $1 AND exchange_exec_id = $2",
            executed_at,
            "exec-t202-1",
        )
        assert exec_row is not None
        assert exec_row["exchange_exec_id"] == "exec-t202-1"
        assert exec_row["order_id"] == order_id
        assert exec_row["trade_id"] == trade_id
        assert exec_row["price"] == max_precision_price
        assert exec_row["qty"] == max_precision_qty
        assert exec_row["fee"] == Decimal("0.00012345")
        assert exec_row["exec_type"] == "open"

        # (h) alembic_version exists. Permissive — successor migrations may
        # advance head; this test asserts the post-0005 artifact shape.
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version is not None
    finally:
        await conn.close()
