"""Integration test for T-526 cooldown gate against real PG.

Mirror :mod:`services.execution.tests.integration.test_shadow_restart` env-gating +
DB seeding patterns shipped T-512b. Verifies that the cooldown gate's SQL string
+ predicates work against a real migrated PostgreSQL + TimescaleDB schema (not
just mock-asserted via fake pool stub like the unit tests). Closes WG#3 (L-021
sub-gap mitigation: local testcontainer execution gate before push).

Per AC#9 plan §Test strategy: INSERT 3 closed paper_trades with strict-negative
``realized_pnl`` staggered within last 60s for a fresh bot, then call
``check_cooldown`` directly. Bus-free; no NATS dependency. Tests the
derived-from-trades design end-to-end (DB SELECT + ORDER BY DESC + status/
realized_pnl predicates) without spinning the full consumer harness.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from packages.scoring import RiskSection
from services.strategy_engine.app.cooldown_gate import check_cooldown

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
        f"T-526 cooldown smoke {bot_id}",
        datetime.now(UTC),
        datetime.now(UTC),
    )


async def _seed_closed_paper_trade(
    conn: object,
    *,
    bot_id: str,
    realized_pnl: Decimal,
    closed_at: datetime,
) -> int:
    """Seed paper_orders + closed paper_trades; return paper_trade_id.

    Mirror :func:`services.execution.tests.integration.test_shadow_restart._seed_paper_trade`
    (T-512b) but immediately marks the trade closed with the given P&L + closed_at,
    bypassing the open→closed FSM transition (DB-level INSERT direct).
    """
    qty = Decimal("0.001")
    entry_price = Decimal("65000")
    exit_price = entry_price + (realized_pnl / qty)
    notional = (qty * entry_price).quantize(Decimal("0.0001"))
    fees = (notional * Decimal("0.0006")).quantize(Decimal("0.0001"))

    order_row = await conn.fetchrow(  # type: ignore[attr-defined]
        "INSERT INTO paper_orders (bot_id, correlation_id, exchange_order_id, "
        "exchange, symbol, side, order_type, qty, price, status, requested_at, "
        "idempotent, meta) VALUES ($1, $2, $3, 'paper', 'BTCUSDT', 'buy', "
        "'market', $4, $5, 'filled', $6, false, '{}'::jsonb) RETURNING id",
        bot_id,
        f"T526-{uuid.uuid4().hex[:8]}",
        f"paper-{uuid.uuid4().hex[:8]}",
        qty,
        entry_price,
        closed_at - timedelta(minutes=1),
    )
    assert order_row is not None
    order_id = int(order_row["id"])

    trade_row = await conn.fetchrow(  # type: ignore[attr-defined]
        "INSERT INTO paper_trades (bot_id, open_order_id, symbol, side, "
        "entry_price, exit_price, qty, notional_usd, fees_paid, realized_pnl, "
        "opened_at, closed_at, status, close_reason, meta) "
        "VALUES ($1, $2, 'BTCUSDT', 'buy', $3, $4, $5, $6, $7, $8, $9, $10, "
        "'closed', 'sl_hit', '{}'::jsonb) RETURNING id",
        bot_id,
        order_id,
        entry_price,
        exit_price.quantize(Decimal("0.0001")),
        qty,
        notional,
        fees,
        realized_pnl,
        closed_at - timedelta(minutes=1),
        closed_at,
    )
    assert trade_row is not None
    return int(trade_row["id"])


async def test_cooldown_gate_against_real_pg_3_consecutive_losses_streak_active(
    pool: asyncpg.Pool,
) -> None:
    """3 consecutive losses within 60s window → streak cooldown active per real PG SELECT.

    Pins:
    - SQL charter invariant (``status='closed' AND realized_pnl IS NOT NULL``)
      filters open / no-pnl rows correctly against real schema.
    - ORDER BY ``closed_at DESC, id DESC`` returns newest-first.
    - LIMIT $2 honored.
    - paper_trades table dispatch (paper exchange_mode).
    - Streak count walked correctly across rows with realized_pnl < 0.
    """
    bot_id = f"cd-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        # 3 consecutive losses, oldest first; closed_at staggered 20s apart.
        await _seed_closed_paper_trade(
            conn,
            bot_id=bot_id,
            realized_pnl=Decimal("-3.00"),
            closed_at=now - timedelta(seconds=40),
        )
        await _seed_closed_paper_trade(
            conn,
            bot_id=bot_id,
            realized_pnl=Decimal("-2.00"),
            closed_at=now - timedelta(seconds=20),
        )
        await _seed_closed_paper_trade(
            conn,
            bot_id=bot_id,
            realized_pnl=Decimal("-1.00"),
            closed_at=now - timedelta(seconds=5),
        )

    cfg = RiskSection(
        cooldown_after_streak_n_losses=3,
        cooldown_after_streak_n_losses_minutes=60,
    )
    decision = await check_cooldown(
        pool=pool,
        bot_id=bot_id,  # type: ignore[arg-type]
        exchange_mode="paper",
        now=now,
        risk_config=cfg,
    )
    assert decision.active is True
    assert decision.reason == "cooldown_after_streak"
    assert decision.streak_count == 3
    # last_loss_at == newest loss closed_at (now - 5s); allow some slack via approx.
    assert decision.last_loss_at is not None
    diff = abs((decision.last_loss_at - (now - timedelta(seconds=5))).total_seconds())
    assert diff < 1.0


async def test_cooldown_gate_against_real_pg_no_closed_trades_inactive(
    pool: asyncpg.Pool,
) -> None:
    """Fresh bot with zero closed trades → inactive (real PG path, not mock)."""
    bot_id = f"cd-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)

    cfg = RiskSection(cooldown_after_loss_minutes=10)
    decision = await check_cooldown(
        pool=pool,
        bot_id=bot_id,  # type: ignore[arg-type]
        exchange_mode="paper",
        now=datetime.now(UTC),
        risk_config=cfg,
    )
    assert decision.active is False
    assert decision.streak_count == 0
    assert decision.last_loss_at is None
