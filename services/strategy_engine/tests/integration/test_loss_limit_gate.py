"""Integration test for T-525a2 daily-loss kill-switch gate against real PG.

Reuses the shared ``conftest.py`` ``pool`` fixture (migrated DB incl. migration
0018 ``bot_kill_switch_state``). Bus-free. Exercises the L-021 `$2::timestamptz`
cast + the COALESCE→Decimal sum + the trip-write + the sticky latch end-to-end
against the real schema (NOT mock-only):

* `test_gate_trips_and_latches_against_real_pg` — closed paper_trades summing
  ≤ -limit today → blocked + `bot_kill_switch_state` row written tripped.
* `test_sticky_latch_blocks_second_call_without_recompute` — second call on
  the persisted latch → blocked (sticky).
* `test_recovering_win_does_not_untrip` — a +win paper_trade after the latch →
  re-call still blocked (the win does not un-trip; recompute skipped).

Per WG#3 (HARD AC): MUST be executed locally with
``POSTGRES_TEST_DSN=postgresql://scalper:devpass@127.0.0.1:5432/postgres
uv run pytest services/strategy_engine/tests/integration/test_loss_limit_gate.py -v``
BEFORE git push — the `$2::timestamptz` cast MUST be exercised vs real PG
(L-021 sub-gap; T-537a1 master-broke-twice anti-pattern).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from packages.db.queries.kill_switch import select_kill_switch_state
from packages.scoring import RiskSection
from services.strategy_engine.app.loss_limit_gate import check_daily_loss_limit

if TYPE_CHECKING:
    import asyncpg

    from packages.core import BotId

pytestmark = pytest.mark.asyncio


async def _seed_bot(conn: object, bot_id: str) -> None:
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO bots (bot_id, display_name, created_at, status, "
        "exchange_mode, config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, 'active', 'paper', 'sha256:test', $4)",
        bot_id,
        f"T-525a2 ll smoke {bot_id}",
        datetime.now(UTC),
        datetime.now(UTC),
    )


async def _seed_closed_paper_trade(
    conn: object, *, bot_id: str, realized_pnl: Decimal, closed_at: datetime
) -> None:
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
        f"T525a2-{uuid.uuid4().hex[:8]}",
        f"paper-{uuid.uuid4().hex[:8]}",
        qty,
        entry_price,
        closed_at - timedelta(minutes=1),
    )
    assert order_row is not None
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO paper_trades (bot_id, open_order_id, symbol, side, "
        "entry_price, exit_price, qty, notional_usd, fees_paid, realized_pnl, "
        "opened_at, closed_at, status, close_reason, meta) "
        "VALUES ($1, $2, 'BTCUSDT', 'buy', $3, $3, $4, $5, $6, $7, $8, $9, "
        "'closed', 'sl_hit', '{}'::jsonb)",
        bot_id,
        int(order_row["id"]),
        entry_price,
        qty,
        notional,
        fees,
        realized_pnl,
        closed_at - timedelta(minutes=1),
        closed_at,
    )


_CFG = RiskSection(daily_loss_limit_usd=Decimal("100"))


async def test_gate_trips_and_latches_against_real_pg(pool: asyncpg.Pool) -> None:
    """Closed paper_trades summing ≤ -100 today → blocked + latch row written.

    Exercises sum_realized_pnl_since (incl. $2::timestamptz cast) + the
    upsert_kill_switch_trip write against the real migrated schema.
    """
    bot_id = f"ll-{uuid.uuid4().hex[:8]}"
    now = datetime(2026, 5, 15, 10, 31, 0, tzinfo=UTC)
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        for pnl, mins in ((Decimal("-40"), 90), (Decimal("-35"), 60), (Decimal("-30"), 5)):
            await _seed_closed_paper_trade(
                conn,
                bot_id=bot_id,
                realized_pnl=pnl,
                closed_at=now - timedelta(minutes=mins),
            )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id=cast("BotId", bot_id),
        exchange_mode="paper",
        now=now,
        risk_config=_CFG,
    )
    assert decision.blocked is True
    assert decision.reason == "daily_loss_limit"
    assert decision.cumulative_loss_usd == Decimal("-105.0000")
    async with pool.acquire() as conn:
        st = await select_kill_switch_state(conn, bot_id=bot_id)
    assert st is not None
    assert st.tripped is True
    assert st.trip_reason == "daily_loss_limit"
    assert st.daily_anchor_date == now.date()


async def test_sticky_latch_blocks_second_call_without_recompute(
    pool: asyncpg.Pool,
) -> None:
    """Second call on the persisted same-day latch → still blocked (sticky)."""
    bot_id = f"ll-{uuid.uuid4().hex[:8]}"
    now = datetime(2026, 5, 15, 11, 0, 0, tzinfo=UTC)
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        await _seed_closed_paper_trade(
            conn,
            bot_id=bot_id,
            realized_pnl=Decimal("-120"),
            closed_at=now - timedelta(minutes=10),
        )
    first = await check_daily_loss_limit(
        pool=pool,
        bot_id=cast("BotId", bot_id),
        exchange_mode="paper",
        now=now,
        risk_config=_CFG,
    )
    assert first.blocked is True
    second = await check_daily_loss_limit(
        pool=pool,
        bot_id=cast("BotId", bot_id),
        exchange_mode="paper",
        now=now,
        risk_config=_CFG,
    )
    assert second.blocked is True
    assert second.reason == "daily_loss_limit"


async def test_recovering_win_does_not_untrip(pool: asyncpg.Pool) -> None:
    """A +win after the latch → re-call still blocked (sticky; no un-trip)."""
    bot_id = f"ll-{uuid.uuid4().hex[:8]}"
    now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        await _seed_closed_paper_trade(
            conn,
            bot_id=bot_id,
            realized_pnl=Decimal("-150"),
            closed_at=now - timedelta(minutes=30),
        )
    assert (
        await check_daily_loss_limit(
            pool=pool,
            bot_id=cast("BotId", bot_id),
            exchange_mode="paper",
            now=now,
            risk_config=_CFG,
        )
    ).blocked is True
    # A big recovering win — recomputed sum would be +50 > -100, but sticky.
    async with pool.acquire() as conn:
        await _seed_closed_paper_trade(
            conn,
            bot_id=bot_id,
            realized_pnl=Decimal("200"),
            closed_at=now - timedelta(minutes=1),
        )
    after = await check_daily_loss_limit(
        pool=pool,
        bot_id=cast("BotId", bot_id),
        exchange_mode="paper",
        now=now + timedelta(minutes=2),
        risk_config=_CFG,
    )
    assert after.blocked is True  # sticky — the +200 win does NOT un-trip
