"""Integration test for migration 0008 (brief §N8, §12.1, §N1, §18.3).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the paper_* mirror tables landed exactly as specified per §12.1 line
1932 ("mirror the shape of their live counterparts") + T-200 plan-doc
4-table commitment (OQ-1 default B; full rationale in
``docs/plans/T-212.md``).

Two test functions per T-204 W#3 grep-friendly naming pattern:

* ``test_migration_0008_creates_paper_mirror_tables`` — full schema +
  smoke E2E + composite-PK uniqueness.
* ``test_paper_executions_hypertable_no_retention_no_compression`` —
  combined retention-negative + compression-negative invariants;
  single grep target locks both regression directions per T-202
  anti-test pattern + OQ-2 default A (mirror live forever).

Schema verbatim per §12.1 + T-202 + T-203 mirror precedent:

* 4 tables: paper_orders (18 cols), paper_trades (21 cols),
  paper_executions (12 cols, hypertable), paper_positions (17 cols).
* 6 FK edges: 2 to bots + 4 paper-internal. NO FKs to live tables.
* 3 mirror-live no-FK: paper_trades.close_order_id (T-202 W#4),
  paper_executions.bot_id (T-202 W#2), paper_positions.bot_id (T-203 W#4).
* paper_executions hypertable on executed_at, 7-day chunks.
* paper_orders has ``exchange`` discriminator column (mirror live
  orders.exchange); other paper_* tables don't (mirror live).
* paper_positions composite PK ``(bot_id, symbol)`` mirror live
  position_state.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

# 4 expected-tuple constants per §12.1 + T-202/T-203 mirror.

_EXPECTED_PAPER_ORDERS_COLUMNS: tuple[tuple[str, str, str], ...] = (
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

_EXPECTED_PAPER_TRADES_COLUMNS: tuple[tuple[str, str, str], ...] = (
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

_EXPECTED_PAPER_EXECUTIONS_COLUMNS: tuple[tuple[str, str, str], ...] = (
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

_EXPECTED_PAPER_POSITIONS_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("bot_id", "text", "NO"),
    ("symbol", "text", "NO"),
    ("trade_id", "bigint", "NO"),
    ("side", "text", "NO"),
    ("entry_price", "numeric", "NO"),
    ("qty", "numeric", "NO"),
    ("remaining_qty", "numeric", "NO"),
    ("sl_price", "numeric", "YES"),
    ("tp_price", "numeric", "YES"),
    ("sl_type", "text", "YES"),
    ("best_price", "numeric", "YES"),
    ("tp_hit", "boolean", "NO"),
    ("trailing_active", "boolean", "NO"),
    ("running_pnl", "numeric", "NO"),
    ("mfe_price", "numeric", "YES"),
    ("mae_price", "numeric", "YES"),
    ("updated_at", "timestamp with time zone", "NO"),
)


_PAPER_TABLES = (
    "paper_orders",
    "paper_trades",
    "paper_executions",
    "paper_positions",
)

_LIVE_TABLE_NAMES = frozenset({"orders", "trades", "executions", "position_state"})


async def _columns_of(conn: asyncpg.Connection, table: str) -> list[tuple[str, str, str]]:
    rows = await conn.fetch(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = $1 "
        "ORDER BY ordinal_position",
        table,
    )
    return [(row["column_name"], row["data_type"], row["is_nullable"]) for row in rows]


async def _pk_columns_of(conn: asyncpg.Connection, table: str) -> list[str]:
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
        table,
    )
    return [row["column_name"] for row in rows]


async def test_migration_0008_creates_paper_mirror_tables(migrated_db_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # JSONB codec for meta round-trip on parent + paper rows.
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

        # (a) Column shapes — 4 tables verbatim per §12.1 + T-202/T-203 mirror.
        assert tuple(await _columns_of(conn, "paper_orders")) == _EXPECTED_PAPER_ORDERS_COLUMNS
        assert tuple(await _columns_of(conn, "paper_trades")) == _EXPECTED_PAPER_TRADES_COLUMNS
        assert (
            tuple(await _columns_of(conn, "paper_executions")) == _EXPECTED_PAPER_EXECUTIONS_COLUMNS
        )
        assert (
            tuple(await _columns_of(conn, "paper_positions")) == _EXPECTED_PAPER_POSITIONS_COLUMNS
        )

        # (b) PKs — simple `id` on paper_orders + paper_trades; composite
        # (executed_at, id) on paper_executions; composite (bot_id, symbol)
        # on paper_positions (mirror live position_state).
        assert await _pk_columns_of(conn, "paper_orders") == ["id"]
        assert await _pk_columns_of(conn, "paper_trades") == ["id"]
        assert await _pk_columns_of(conn, "paper_executions") == ["executed_at", "id"]
        assert await _pk_columns_of(conn, "paper_positions") == ["bot_id", "symbol"]

        # (c) FK chain — 6 edges per §12.1 + T-202/T-203 mirror.
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
            "  AND tc.table_name = ANY($1::text[])",
            list(_PAPER_TABLES),
        )
        fks = {
            (row["child_table"], row["child_column"], row["parent_table"], row["parent_column"])
            for row in fk_rows
        }
        assert ("paper_orders", "bot_id", "bots", "bot_id") in fks
        assert ("paper_trades", "bot_id", "bots", "bot_id") in fks
        assert ("paper_trades", "open_order_id", "paper_orders", "id") in fks
        assert ("paper_executions", "order_id", "paper_orders", "id") in fks
        assert ("paper_executions", "trade_id", "paper_trades", "id") in fks
        assert ("paper_positions", "trade_id", "paper_trades", "id") in fks

        # Cross-cutting anti-FK: NO FK from any paper_* column to any LIVE
        # table per §12.1 + TASKS.md line 81 verbatim ("no-FK to live tables").
        no_live_fk = {
            (child, column) for child, column, parent, _ in fks if parent in _LIVE_TABLE_NAMES
        }
        assert not no_live_fk, (
            f"paper_* columns must NOT FK to live tables per §12.1 + TASKS.md "
            f"line 81; found: {no_live_fk}"
        )

        # Mirror-live anti-FK assertions per T-202 W#4 / T-202 W#2 / T-203 W#4
        # via direct pg_constraint query.
        async def _fk_count_on(table: str, column: str) -> int:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM pg_constraint c "
                "JOIN pg_class t ON c.conrelid = t.oid "
                "JOIN pg_attribute a ON a.attrelid = t.oid "
                "                   AND a.attnum = ANY(c.conkey) "
                "WHERE t.relname = $1 AND c.contype = 'f' AND a.attname = $2",
                table,
                column,
            )
            assert isinstance(value, int)
            return value

        assert await _fk_count_on("paper_trades", "close_order_id") == 0, (
            "paper_trades.close_order_id must have NO FK per T-202 W#4"
        )
        assert await _fk_count_on("paper_executions", "bot_id") == 0, (
            "paper_executions.bot_id must have NO FK per T-202 W#2"
        )
        assert await _fk_count_on("paper_positions", "bot_id") == 0, (
            "paper_positions.bot_id must have NO FK per T-203 W#4"
        )

        # (d) Server defaults — paper_orders/paper_trades meta = '{}'::jsonb;
        # paper_positions tp_hit/trailing_active false, running_pnl 0; NO
        # server_default on any TIMESTAMPTZ column per §N1.
        defaults_orders = {
            row["column_name"]: row["column_default"]
            for row in await conn.fetch(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'paper_orders'"
            )
        }
        assert defaults_orders["meta"] == "'{}'::jsonb"
        for ts_col in ("requested_at", "placed_at", "filled_at", "closed_at"):
            assert defaults_orders[ts_col] is None, (
                f"paper_orders.{ts_col} must have NO server_default per §N1"
            )

        defaults_trades = {
            row["column_name"]: row["column_default"]
            for row in await conn.fetch(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'paper_trades'"
            )
        }
        assert defaults_trades["meta"] == "'{}'::jsonb"
        for ts_col in ("opened_at", "closed_at"):
            assert defaults_trades[ts_col] is None, (
                f"paper_trades.{ts_col} must have NO server_default per §N1"
            )

        defaults_positions = {
            row["column_name"]: row["column_default"]
            for row in await conn.fetch(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'paper_positions'"
            )
        }
        assert defaults_positions["tp_hit"] == "false"
        assert defaults_positions["trailing_active"] == "false"
        assert defaults_positions["running_pnl"] == "0"
        assert defaults_positions["updated_at"] is None, (
            "paper_positions.updated_at must have NO server_default per §N1"
        )

        defaults_executions = {
            row["column_name"]: row["column_default"]
            for row in await conn.fetch(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'paper_executions'"
            )
        }
        assert defaults_executions["executed_at"] is None, (
            "paper_executions.executed_at must have NO server_default per §N1"
        )

        # (e) Indexes — subset assertion per T-204 W#1 vendor-metadata robustness.
        index_defs = {
            row["indexname"]: row["indexdef"]
            for row in await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = ANY($1::text[])",
                list(_PAPER_TABLES),
            )
        }
        expected_indexes = {
            "paper_orders_bot_status",
            "paper_orders_correlation",
            "paper_trades_bot_status",
            "paper_trades_closed_at",
            "paper_executions_exchange_id",
            "paper_executions_trade",
        }
        actual_indexes = set(index_defs)
        assert expected_indexes <= actual_indexes, (
            f"missing paper_* indexes: {expected_indexes - actual_indexes}"
        )
        assert "(bot_id, status)" in index_defs["paper_orders_bot_status"]
        assert "(correlation_id)" in index_defs["paper_orders_correlation"]
        assert "(bot_id, status)" in index_defs["paper_trades_bot_status"]
        assert "(closed_at DESC)" in index_defs["paper_trades_closed_at"]
        assert "WHERE (status = 'closed'::text)" in index_defs["paper_trades_closed_at"]
        assert "UNIQUE" in index_defs["paper_executions_exchange_id"]
        assert "(exchange_exec_id, executed_at)" in index_defs["paper_executions_exchange_id"]
        assert "(trade_id)" in index_defs["paper_executions_trade"]

        # (f) paper_executions is a hypertable with 7-day chunks on executed_at.
        time_interval = await conn.fetchval(
            "SELECT time_interval FROM timescaledb_information.dimensions "
            "WHERE hypertable_schema = 'public' "
            "  AND hypertable_name = 'paper_executions' "
            "  AND column_name = 'executed_at'"
        )
        assert time_interval == timedelta(days=7), (
            f"expected 7-day chunk_time_interval, got {time_interval!r}"
        )

        # (g) paper_orders / paper_trades / paper_positions are NOT hypertables.
        for not_hypertable in ("paper_orders", "paper_trades", "paper_positions"):
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_schema = 'public' AND hypertable_name = $1",
                not_hypertable,
            )
            assert count == 0, f"{not_hypertable} must NOT be a hypertable"

        # (h) Smoke E2E — INSERT chain bots → paper_orders → paper_trades →
        # paper_executions → paper_positions; NUMERIC(30,12) max-precision
        # round-trip per T-202 W#5 / T-203 W#3 precedent.
        bot_id = f"test_t212_{uuid.uuid4().hex[:8]}"
        await conn.execute(
            "INSERT INTO bots "
            "(bot_id, display_name, created_at, status, exchange_mode, "
            " config_hash, config_applied_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            bot_id,
            "T-212 smoke",
            datetime(2026, 4, 27, tzinfo=UTC),
            "active",
            "paper",
            "sha256:smoke",
            datetime(2026, 4, 27, tzinfo=UTC),
        )

        max_precision_qty = Decimal("0.123456789012")
        max_precision_price = Decimal("65000.123456789012")

        order_id = await conn.fetchval(
            "INSERT INTO paper_orders "
            "(bot_id, correlation_id, exchange, symbol, side, order_type, "
            " qty, price, status, requested_at, idempotent, meta) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
            "RETURNING id",
            bot_id,
            "corr-t212",
            "paper",
            "BTCUSDT",
            "buy",
            "market",
            max_precision_qty,
            max_precision_price,
            "filled",
            datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
            False,
            {"source": "T-212"},
        )
        assert isinstance(order_id, int)

        trade_id = await conn.fetchval(
            "INSERT INTO paper_trades "
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
            {},
        )
        assert isinstance(trade_id, int)

        executed_at = datetime(2026, 4, 27, 12, 0, 2, tzinfo=UTC)
        await conn.execute(
            "INSERT INTO paper_executions "
            "(exchange_exec_id, order_id, trade_id, bot_id, symbol, side, "
            " price, qty, fee, exec_type, executed_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            "exec-t212-1",
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

        await conn.execute(
            "INSERT INTO paper_positions "
            "(bot_id, symbol, trade_id, side, entry_price, qty, remaining_qty, "
            " updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            bot_id,
            "BTCUSDT",
            trade_id,
            "buy",
            max_precision_price,
            max_precision_qty,
            max_precision_qty,
            datetime(2026, 4, 27, 12, 0, 3, tzinfo=UTC),
        )

        # SELECT-back round-trip — paper_orders.exchange='paper'.
        order_row = await conn.fetchrow(
            "SELECT exchange, qty, price, idempotent, meta FROM paper_orders WHERE id = $1",
            order_id,
        )
        assert order_row is not None
        assert order_row["exchange"] == "paper"
        assert order_row["qty"] == max_precision_qty
        assert order_row["price"] == max_precision_price
        assert order_row["idempotent"] is False
        assert order_row["meta"] == {"source": "T-212"}

        # paper_trades round-trip with NUMERIC(20,4) USD-denominated.
        trade_row = await conn.fetchrow(
            "SELECT entry_price, qty, notional_usd FROM paper_trades WHERE id = $1",
            trade_id,
        )
        assert trade_row is not None
        assert trade_row["entry_price"] == max_precision_price
        assert trade_row["qty"] == max_precision_qty
        assert trade_row["notional_usd"] == Decimal("8000.0000")

        # paper_executions round-trip with NUMERIC(20,8) fee.
        exec_row = await conn.fetchrow(
            "SELECT price, qty, fee, exec_type FROM paper_executions "
            "WHERE executed_at = $1 AND exchange_exec_id = $2",
            executed_at,
            "exec-t212-1",
        )
        assert exec_row is not None
        assert exec_row["price"] == max_precision_price
        assert exec_row["qty"] == max_precision_qty
        assert exec_row["fee"] == Decimal("0.00012345")
        assert exec_row["exec_type"] == "open"

        # paper_positions round-trip — server defaults populated.
        pos_row = await conn.fetchrow(
            "SELECT trade_id, tp_hit, trailing_active, running_pnl "
            "FROM paper_positions WHERE bot_id = $1 AND symbol = $2",
            bot_id,
            "BTCUSDT",
        )
        assert pos_row is not None
        assert pos_row["trade_id"] == trade_id
        assert pos_row["tp_hit"] is False
        assert pos_row["trailing_active"] is False
        assert pos_row["running_pnl"] == Decimal("0")

        # (i) alembic_version exists (permissive — successor migrations may
        # advance head).
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version is not None
    finally:
        await conn.close()


async def test_paper_executions_hypertable_no_retention_no_compression(
    migrated_db_dsn: str,
) -> None:
    """Combined retention-negative + compression-negative invariants on
    ``paper_executions`` per OQ-2 default A.

    Single grep target locks both regression directions:

    * **Negative (retention)**: NO retention policy IS present per §18.3
      mirror live forever. Catches accidental copy-paste from 0003/0004
      retention block.
    * **Negative (compression)**: NO compression policy IS present per
      §18.3 (compression for ``ohlc_1m`` + ``features`` only). Catches
      accidental copy-paste from 0003/0004 compression block.

    Mirror T-202 anti-test pattern (NEGATION of test_0004 / 0007 positive
    retention assertion).
    """
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        policy_targets = {
            (row["proc_name"], row["table_name"])
            for row in await conn.fetch(
                "SELECT j.proc_name, h.table_name "
                "FROM _timescaledb_config.bgw_job j "
                "JOIN _timescaledb_catalog.hypertable h "
                "  ON h.id = (j.config->>'hypertable_id')::int "
                "WHERE h.table_name = 'paper_executions'"
            )
        }
        assert ("policy_retention", "paper_executions") not in policy_targets, (
            "paper_executions must NOT have policy_retention per §18.3 mirror "
            "live forever (live executions kept forever)"
        )
        assert ("policy_compression", "paper_executions") not in policy_targets, (
            "paper_executions must NOT have policy_compression per §18.3 "
            "(compression specified for ohlc_1m + features only)"
        )
    finally:
        await conn.close()
