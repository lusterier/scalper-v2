"""Integration test for migration 0020 (T-533a / trades.lifecycle_state).

Runs against a throwaway DB already migrated to head (includes 0020).
Verifies:

* ``trades.lifecycle_state`` exists, ``data_type='text'``, nullable
  (additive observable column — T-533 OQ-1=A).
* **Backfill correctness + L-003 golden cross-check** (the central T-533a
  artifact): downgrade to 0019 (drops the column), seed the full legacy
  combination matrix (orders→trades→position_state FK chain), upgrade
  0020 (re-adds + backfills), then for every seeded row assert the DB
  ``lifecycle_state`` == a **hand-authored expected enum value** AND ==
  ``packages.db.queries.lifecycle.derive_lifecycle_state(...)`` on the
  same inputs. The hand-authored expected values are independent of both
  the migration SQL CASE and the Python helper → this is a true
  equivalence proof (SQL ≡ Python ≡ hand), not implementation-against-
  itself.
* Explicit ``downgrade 0019`` target per L-012 (NEVER relative ``-1``);
  downgrade drops the column.

Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset.

Per L-021 active control: this testcontainer test MUST be run locally with
``POSTGRES_TEST_DSN=... uv run pytest tests/integration/migrations/test_0020_migration.py -v``
BEFORE git push (CI must not be the first execution surface).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from packages.core import TradeLifecycleState
from packages.db.queries.lifecycle import derive_lifecycle_state

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _alembic(target: str, dsn: str) -> None:
    proc = subprocess.run(
        ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), target.split()[0], target.split()[1]],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "POSTGRES_URL": dsn},
        cwd=_REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.asyncio
async def test_0020_adds_lifecycle_state_text_column(migrated_db_dsn: str) -> None:
    """Head (incl. 0020) → trades.lifecycle_state TEXT, nullable (additive)."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        row = await conn.fetchrow(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'trades' AND column_name = 'lifecycle_state'
            """,
        )
        assert row is not None, "lifecycle_state column missing"
        assert row["data_type"] == "text"
        assert row["is_nullable"] == "YES"
    finally:
        await conn.close()


# (symbol, status, close_reason, sl_type, tp_hit, trailing_active, has_ps) → expected
_MATRIX: tuple[
    tuple[str, str, str | None, str | None, bool, bool, bool, TradeLifecycleState], ...
] = (
    ("ERRUSDT", "error", None, None, False, False, False, TradeLifecycleState.FAILED),
    (
        "RCNUSDT",
        "closed",
        "reconcile_gone",
        None,
        False,
        False,
        False,
        TradeLifecycleState.RECONCILED,
    ),
    ("CLMUSDT", "closed", "manual", None, False, False, False, TradeLifecycleState.CLOSED),
    ("CLEUSDT", "closed", "emergency", None, False, False, False, TradeLifecycleState.CLOSED),
    ("CLNUSDT", "closed", None, None, False, False, False, TradeLifecycleState.CLOSED),
    ("ORPUSDT", "open", None, None, False, False, False, TradeLifecycleState.ORPHANED),
    ("TRLUSDT", "open", None, "trail", False, False, True, TradeLifecycleState.TRAILING_ACTIVE),
    ("TRFUSDT", "open", None, "protective", False, True, True, TradeLifecycleState.TRAILING_ACTIVE),
    ("BEEUSDT", "open", None, "be", False, False, True, TradeLifecycleState.BREAKEVEN_SET),
    (
        "PCLUSDT",
        "open",
        None,
        "protective",
        True,
        False,
        True,
        TradeLifecycleState.PARTIALLY_CLOSED,
    ),
    ("OPNUSDT", "open", None, "protective", False, False, True, TradeLifecycleState.OPEN),
)


@pytest.mark.asyncio
async def test_backfill_sql_equals_python_and_hand(migrated_db_dsn: str) -> None:
    """L-003 golden cross-check + backfill correctness: downgrade 0019 →
    seed full legacy matrix → upgrade 0020 → DB == hand-expected ==
    derive_lifecycle_state (SQL ≡ Python ≡ hand-authored)."""
    await asyncio.to_thread(_alembic, "downgrade 0019", migrated_db_dsn)

    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        bot_id = "lc-bot"
        opened = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
        # FK chain: bots → orders → trades → position_state.
        await conn.execute(
            "INSERT INTO bots (bot_id, display_name, created_at, status, "
            "exchange_mode, config_hash, config_applied_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            bot_id,
            "T-533a lifecycle backfill",
            opened,
            "active",
            "paper",
            "sha256:t533a",
            opened,
        )
        seeded: list[
            tuple[int, str, str | None, bool, str | None, bool, bool, TradeLifecycleState]
        ] = []
        for symbol, status, close_reason, sl_type, tp_hit, trailing, has_ps, expected in _MATRIX:
            order_id = await conn.fetchval(
                "INSERT INTO orders (bot_id, correlation_id, exchange, symbol, side, "
                "order_type, qty, price, status, requested_at, idempotent, meta) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING id",
                bot_id,
                f"corr-{symbol}",
                "bybit",
                symbol,
                "buy",
                "market",
                Decimal("0.01"),
                Decimal("100.00"),
                "filled",
                opened,
                False,
                "{}",  # meta jsonb — raw asyncpg (no codec) needs JSON str (L-011/L-013)
            )
            trade_id = await conn.fetchval(
                "INSERT INTO trades (bot_id, open_order_id, symbol, side, entry_price, "
                "qty, notional_usd, opened_at, status, close_reason, meta) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id",
                bot_id,
                order_id,
                symbol,
                "buy",
                Decimal("100.00"),
                Decimal("0.01"),
                Decimal("1.0000"),
                opened,
                status,
                close_reason,
                "{}",  # meta jsonb — raw asyncpg (no codec) needs JSON str (L-011/L-013)
            )
            assert isinstance(trade_id, int)
            if has_ps:
                await conn.execute(
                    "INSERT INTO position_state (bot_id, symbol, trade_id, side, "
                    "entry_price, qty, remaining_qty, sl_type, tp_hit, trailing_active, "
                    "updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    bot_id,
                    symbol,
                    trade_id,
                    "buy",
                    Decimal("100.00"),
                    Decimal("0.01"),
                    Decimal("0.01"),
                    sl_type,
                    tp_hit,
                    trailing,
                    opened,
                )
            seeded.append(
                (trade_id, status, close_reason, tp_hit, sl_type, trailing, has_ps, expected),
            )
    finally:
        await conn.close()

    await asyncio.to_thread(_alembic, "upgrade 0020", migrated_db_dsn)

    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        for trade_id, status, close_reason, tp_hit, sl_type, trailing, has_ps, expected in seeded:
            db_val = await conn.fetchval(
                "SELECT lifecycle_state FROM trades WHERE id = $1",
                trade_id,
            )
            python_val = derive_lifecycle_state(
                status=status,
                close_reason=close_reason,
                tp_hit=tp_hit if has_ps else None,
                sl_type=sl_type if has_ps else None,
                trailing_active=trailing if has_ps else None,
                has_position_state=has_ps,
            )
            assert db_val == expected.value, (
                f"backfill drift trade_id={trade_id}: db={db_val!r} expected={expected.value!r}"
            )
            assert db_val == python_val.value, (
                f"L-003 SQL≠Python trade_id={trade_id}: sql={db_val!r} py={python_val.value!r}"
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_downgrade_0019_drops_lifecycle_state(migrated_db_dsn: str) -> None:
    """L-012 explicit downgrade 0019 target (NOT relative -1) → column dropped."""
    await asyncio.to_thread(_alembic, "downgrade 0019", migrated_db_dsn)
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'trades' AND column_name = 'lifecycle_state')",
        )
        assert exists is False
    finally:
        await conn.close()
