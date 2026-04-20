"""Integration test for migration 0001 (brief §N8, §7.4).

Runs ``alembic upgrade head`` against a throwaway database and
verifies:

* TimescaleDB extension is installed.
* The three config-plane tables (``bots``, ``bot_configs``,
  ``symbol_map``) exist.
* ``config_hash`` is NOT NULL on ``bots`` and ``bot_configs``
  (§7.2 schema invariant).
* ``symbol_map`` carries the two seed rows from Appendix B.4.
* An ``alembic_version`` row exists (proof Alembic ran). The specific
  head revision advances as later migrations land — each successor's
  own test_00NN_migration.py asserts head matches its revision. This
  test therefore does not over-specify the tail.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset — see
``conftest.py`` docstring.
"""

from __future__ import annotations

import asyncpg


async def test_migration_0001_creates_expected_schema(migrated_db_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        ext_row = await conn.fetchrow(
            "SELECT extname FROM pg_extension WHERE extname = 'timescaledb'"
        )
        assert ext_row is not None, "timescaledb extension must be installed"

        table_names = {
            row["table_name"]
            for row in await conn.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
        }
        assert {"bots", "bot_configs", "symbol_map"} <= table_names

        nullability = {
            (row["table_name"], row["column_name"]): row["is_nullable"]
            for row in await conn.fetch(
                "SELECT table_name, column_name, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "  AND table_name IN ('bots', 'bot_configs') "
                "  AND column_name = 'config_hash'"
            )
        }
        assert nullability[("bots", "config_hash")] == "NO"
        assert nullability[("bot_configs", "config_hash")] == "NO"

        seed_rows = await conn.fetch(
            "SELECT input_symbol, canonical_symbol, exchange_source "
            "FROM symbol_map ORDER BY input_symbol"
        )
        assert [dict(r) for r in seed_rows] == [
            {
                "input_symbol": "BTCUSDT.P",
                "canonical_symbol": "BTCUSDT",
                "exchange_source": "binance",
            },
            {
                "input_symbol": "ETHUSDT.P",
                "canonical_symbol": "ETHUSDT",
                "exchange_source": "binance",
            },
        ]

        version = await conn.fetchval("SELECT version_num FROM alembic_version")
        assert version is not None
    finally:
        await conn.close()
