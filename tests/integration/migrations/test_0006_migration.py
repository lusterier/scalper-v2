"""Integration test for migration 0006 (brief §N8, §7.2, §N1, §18.3).

Runs ``alembic upgrade head`` against a throwaway database and verifies
the ``position_state`` regular table landed exactly as specified:

* 17-column shape per §7.2 lines 1058-1080 verbatim, with the right
  nullability and types.
* Composite PK ``(bot_id, symbol)``.
* Single FK ``trade_id → trades.id``.
* Anti-FK on ``position_state.bot_id`` (no FK on ``bots(bot_id)`` per
  §7.2 verbatim, mirror T-202 W#2 precedent).
* Server defaults: ``tp_hit='false'``, ``trailing_active='false'``,
  ``running_pnl='0'`` (server-side).
* **NO** server_default on ``updated_at`` per §N1 invariant —
  application sets via ``packages.core.now_utc()``.
* No secondary indexes (only the PK index ``position_state_pkey``).
* No hypertable (regular table per §7.2 line 1058 header).
* Smoke E2E along the FK chain bots → orders → trades →
  position_state with NUMERIC(30,12) max-precision smoke.
* Composite-PK uniqueness: second INSERT with same ``(bot_id,
  symbol)`` raises ``asyncpg.exceptions.UniqueViolationError``.
* DELETE-by-PK lifetime check: row removed by PK; SELECT WHERE PK
  returns 0 rows (locks the "row-lifetime = position-lifetime;
  close = DELETE" invariant T-217 / T-221 rely on).
* The ``alembic_version`` row exists (permissive — successor
  migrations legitimately advance head).

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
import pytest

_EXPECTED_POSITION_STATE_COLUMNS: tuple[tuple[str, str, str], ...] = (
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


async def test_migration_0006_creates_position_state(migrated_db_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        # JSONB codec for parent-row INSERTs (orders.meta + trades.meta).
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
                "WHERE table_schema = 'public' AND table_name = 'position_state' "
                "ORDER BY ordinal_position"
            )
        ]
        assert tuple(columns) == _EXPECTED_POSITION_STATE_COLUMNS

        # (b) Composite PK (bot_id, symbol).
        pk_columns = [
            row["column_name"]
            for row in await conn.fetch(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                " AND tc.table_schema = kcu.table_schema "
                "WHERE tc.table_schema = 'public' "
                "  AND tc.table_name = 'position_state' "
                "  AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position"
            )
        ]
        assert pk_columns == ["bot_id", "symbol"]

        # (c) FK chain — exactly one FK: position_state.trade_id → trades.id.
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
            "  AND tc.table_name = 'position_state'"
        )
        fks = {
            (row["child_table"], row["child_column"], row["parent_table"], row["parent_column"])
            for row in fk_rows
        }
        assert fks == {("position_state", "trade_id", "trades", "id")}

        # Anti-FK on position_state.bot_id per §7.2 verbatim — mirror T-202
        # W#2 precedent. Query pg_constraint directly to lock the invariant.
        bot_id_fk_count = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_constraint c "
            "JOIN pg_class t ON c.conrelid = t.oid "
            "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey) "
            "WHERE t.relname = 'position_state' "
            "  AND c.contype = 'f' "
            "  AND a.attname = 'bot_id'"
        )
        assert bot_id_fk_count == 0, (
            "position_state.bot_id must have NO FK on bots(bot_id) per §7.2 verbatim"
        )

        # (d) Server defaults — three populated, updated_at IS NULL.
        defaults = {
            row["column_name"]: row["column_default"]
            for row in await conn.fetch(
                "SELECT column_name, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'position_state'"
            )
        }
        assert defaults["tp_hit"] == "false"
        assert defaults["trailing_active"] == "false"
        assert defaults["running_pnl"] == "0"
        # §N1 invariant: NO server_default on updated_at (no CURRENT_TIMESTAMP / NOW()).
        assert defaults["updated_at"] is None

        # (e) NO secondary indexes — only the PK index.
        index_names = {
            row["indexname"]
            for row in await conn.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'public' AND tablename = 'position_state'"
            )
        }
        assert index_names == {"position_state_pkey"}, (
            f"expected only the PK index, got {sorted(index_names)}"
        )

        # (f) NO hypertable on position_state (regular table per §7.2 line 1058).
        hypertable_count = await conn.fetchval(
            "SELECT COUNT(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = 'public' AND hypertable_name = 'position_state'"
        )
        assert hypertable_count == 0

        # (g) Smoke E2E — INSERT along FK chain, max-precision NUMERIC,
        # composite-PK uniqueness, DELETE-by-PK lifetime check.
        bot_id = f"test_t203_{uuid.uuid4().hex[:8]}"
        await conn.execute(
            "INSERT INTO bots "
            "(bot_id, display_name, created_at, status, exchange_mode, "
            " config_hash, config_applied_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            bot_id,
            "T-203 smoke",
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
            "corr-t203",
            "bybit",
            "BTCUSDT",
            "buy",
            "market",
            max_precision_qty,
            max_precision_price,
            "filled",
            datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC),
            False,
            {"source": "T-203"},
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
            {},
        )
        assert isinstance(trade_id, int)

        # Insert position_state — only the NOT-NULL columns; verify server
        # defaults populate tp_hit / trailing_active / running_pnl.
        updated_at_initial = datetime(2026, 4, 27, 12, 0, 2, tzinfo=UTC)
        await conn.execute(
            "INSERT INTO position_state "
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
            updated_at_initial,
        )

        # SELECT back — verify NUMERIC precision + boolean/numeric defaults.
        ps_row = await conn.fetchrow(
            "SELECT trade_id, side, entry_price, qty, remaining_qty, "
            "       sl_price, tp_price, sl_type, best_price, "
            "       tp_hit, trailing_active, running_pnl, "
            "       mfe_price, mae_price, updated_at "
            "FROM position_state WHERE bot_id = $1 AND symbol = $2",
            bot_id,
            "BTCUSDT",
        )
        assert ps_row is not None
        assert ps_row["trade_id"] == trade_id
        assert ps_row["side"] == "buy"
        assert ps_row["entry_price"] == max_precision_price
        assert ps_row["qty"] == max_precision_qty
        assert ps_row["remaining_qty"] == max_precision_qty
        # Nullable columns left unset are NULL.
        assert ps_row["sl_price"] is None
        assert ps_row["tp_price"] is None
        assert ps_row["sl_type"] is None
        assert ps_row["best_price"] is None
        assert ps_row["mfe_price"] is None
        assert ps_row["mae_price"] is None
        # Server defaults populated.
        assert ps_row["tp_hit"] is False
        assert ps_row["trailing_active"] is False
        assert ps_row["running_pnl"] == Decimal("0")
        assert ps_row["updated_at"] == updated_at_initial

        # Composite-PK uniqueness: second INSERT with same (bot_id, symbol)
        # raises asyncpg.exceptions.UniqueViolationError. Locks the
        # invariant that PK is (bot_id, symbol), not a surrogate.
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(
                "INSERT INTO position_state "
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
                updated_at_initial,
            )

        # DELETE-by-PK lifetime check: row-lifetime = position-lifetime;
        # close = DELETE. T-217 / T-221 rely on this invariant.
        await conn.execute(
            "DELETE FROM position_state WHERE bot_id = $1 AND symbol = $2",
            bot_id,
            "BTCUSDT",
        )
        post_delete_count = await conn.fetchval(
            "SELECT COUNT(*) FROM position_state WHERE bot_id = $1 AND symbol = $2",
            bot_id,
            "BTCUSDT",
        )
        assert post_delete_count == 0

        # (h) alembic_version exists (permissive — successor migrations
        # may advance head).
        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version is not None
    finally:
        await conn.close()
