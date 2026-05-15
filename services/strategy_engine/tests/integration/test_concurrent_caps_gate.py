"""Integration test for T-524 concurrent-caps gate against real PG.

Mirror :mod:`services.strategy_engine.tests.integration.test_cooldown_gate`
(T-526) env-gating + DB seeding patterns; reuses the shared
``conftest.py`` ``pool`` / ``migrated_db_dsn`` fixtures. Verifies that
``count_open_trades`` + ``check_concurrent_caps`` work against a real migrated
PostgreSQL + TimescaleDB schema (not just mock-asserted). Closes WG#3 (L-021
sub-gap mitigation: local testcontainer execution before push).

Per AC#9 plan §Test strategy: seed N OPEN paper_trades for a fresh bot, then
call ``check_concurrent_caps`` directly. Bus-free; no NATS dependency.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from packages.scoring import RiskSection
from services.strategy_engine.app.concurrent_caps_gate import check_concurrent_caps

if TYPE_CHECKING:
    import asyncpg


pytestmark = pytest.mark.asyncio


async def _seed_bot(conn: object, bot_id: str) -> None:
    """Insert a ``bots`` row so paper_orders.bot_id FK passes."""
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO bots (bot_id, display_name, created_at, status, "
        "exchange_mode, config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, 'active', 'paper', 'sha256:test', $4)",
        bot_id,
        f"T-524 caps smoke {bot_id}",
        datetime.now(UTC),
        datetime.now(UTC),
    )


async def _seed_open_paper_trade(conn: object, *, bot_id: str) -> int:
    """Seed paper_orders + an OPEN paper_trades row; return paper_trade_id.

    Mirror :func:`...test_cooldown_gate._seed_closed_paper_trade` (T-526) but
    leaves the trade ``status='open'`` (no ``closed_at`` / ``realized_pnl``) so
    it counts toward the concurrent-caps gate.
    """
    qty = Decimal("0.001")
    entry_price = Decimal("65000")
    notional = (qty * entry_price).quantize(Decimal("0.0001"))
    fees = (notional * Decimal("0.0006")).quantize(Decimal("0.0001"))

    order_row = await conn.fetchrow(  # type: ignore[attr-defined]
        "INSERT INTO paper_orders (bot_id, correlation_id, exchange_order_id, "
        "exchange, symbol, side, order_type, qty, price, status, requested_at, "
        "idempotent, meta) VALUES ($1, $2, $3, 'paper', 'BTCUSDT', 'buy', "
        "'market', $4, $5, 'filled', $6, false, '{}'::jsonb) RETURNING id",
        bot_id,
        f"T524-{uuid.uuid4().hex[:8]}",
        f"paper-{uuid.uuid4().hex[:8]}",
        qty,
        entry_price,
        datetime.now(UTC),
    )
    assert order_row is not None
    order_id = int(order_row["id"])

    trade_row = await conn.fetchrow(  # type: ignore[attr-defined]
        "INSERT INTO paper_trades (bot_id, open_order_id, symbol, side, "
        "entry_price, qty, notional_usd, fees_paid, opened_at, status, meta) "
        "VALUES ($1, $2, 'BTCUSDT', 'buy', $3, $4, $5, $6, $7, 'open', "
        "'{}'::jsonb) RETURNING id",
        bot_id,
        order_id,
        entry_price,
        qty,
        notional,
        fees,
        datetime.now(UTC),
    )
    assert trade_row is not None
    return int(trade_row["id"])


async def test_caps_gate_real_pg_per_bot_cap_blocks_at_count_ge_cap(
    pool: asyncpg.Pool,
) -> None:
    """3 open paper_trades + cap=3 → blocked (>= predicate against real PG SELECT).

    Pins: count_open_trades charter `status='open'` + per-bot `bot_id=$1`
    filter against real schema; >= block predicate; paper_trades dispatch.
    """
    bot_id = f"cap-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        for _ in range(3):
            await _seed_open_paper_trade(conn, bot_id=bot_id)

    decision = await check_concurrent_caps(
        pool=pool,
        bot_id=bot_id,  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_per_bot=3),
    )
    assert decision.blocked is True
    assert decision.reason == "max_open_trades_per_bot"
    assert decision.current_count == 3
    assert decision.cap_limit == 3


async def test_caps_gate_real_pg_under_cap_passes(
    pool: asyncpg.Pool,
) -> None:
    """2 open paper_trades + cap=3 → not blocked (real PG path)."""
    bot_id = f"cap-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        await _seed_open_paper_trade(conn, bot_id=bot_id)
        await _seed_open_paper_trade(conn, bot_id=bot_id)

    decision = await check_concurrent_caps(
        pool=pool,
        bot_id=bot_id,  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_per_bot=3),
    )
    assert decision.blocked is False
