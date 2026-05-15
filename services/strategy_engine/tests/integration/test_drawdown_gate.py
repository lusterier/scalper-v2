"""Integration test for T-525b max-drawdown gate against real PG.

Reuses the shared ``conftest.py`` ``pool`` fixture (migrated DB incl. migration
0018 ``bot_kill_switch_state``). Bus-free. Exercises the NEW window-function
SQL (`select_pnl_peak_and_current` — running prefix-sum + MAX peak) + the
`(peak-current)/peak` Decimal division + the `max_drawdown` trip-write
end-to-end against the real schema (NOT mock-only):

* `test_gate_trips_and_latches_against_real_pg` — ordered closed paper_trades
  building a profit peak then a give-back ≥ limit → blocked + a
  `bot_kill_switch_state` row written tripped reason='max_drawdown'.
* `test_sticky_latch_blocks_second_call_without_recompute` — second call on
  the persisted latch → blocked (sticky).
* `test_peak_le_zero_never_blocked` — a never-profitable bot (only losses) →
  not blocked (OQ-A peak>0 guard vs real PG).

Per WG#9 (HARD AC): MUST be executed locally with
``POSTGRES_TEST_DSN=postgresql://scalper:devpass@127.0.0.1:5432/postgres
uv run pytest services/strategy_engine/tests/integration/test_drawdown_gate.py -v``
BEFORE git push — a window-function query is exactly the non-trivial-SQL class
L-008 mandates a real-PG run for (T-537a1 master-broke-twice anti-pattern).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from packages.db.queries.kill_switch import select_kill_switch_state
from packages.scoring import RiskSection
from services.strategy_engine.app.drawdown_gate import check_max_drawdown

if TYPE_CHECKING:
    import asyncpg

    from packages.core import BotId

pytestmark = pytest.mark.asyncio

_CFG = RiskSection(max_drawdown_pct=Decimal("0.20"))


async def _seed_bot(conn: object, bot_id: str) -> None:
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO bots (bot_id, display_name, created_at, status, "
        "exchange_mode, config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, 'active', 'paper', 'sha256:test', $4)",
        bot_id,
        f"T-525b dd smoke {bot_id}",
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
        f"T525b-{uuid.uuid4().hex[:8]}",
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


async def test_gate_trips_and_latches_against_real_pg(pool: asyncpg.Pool) -> None:
    """Profit peak 120 then give-back to 75 (0.375 ≥ 0.20) → blocked + latch.

    Exercises the window-fn running prefix-sum + MAX peak + the Decimal
    (peak-current)/peak division against the real migrated schema.
    """
    bot_id = f"dd-{uuid.uuid4().hex[:8]}"
    base = datetime(2026, 5, 15, 9, 0, 0, tzinfo=UTC)
    # Ordered: +50, +70 (peak=120), -30, -15 → current=75; drawdown=(120-75)/120=0.375.
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        for i, pnl in enumerate((Decimal("50"), Decimal("70"), Decimal("-30"), Decimal("-15"))):
            await _seed_closed_paper_trade(
                conn,
                bot_id=bot_id,
                realized_pnl=pnl,
                closed_at=base + timedelta(minutes=10 * i),
            )
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", bot_id),
        exchange_mode="paper",
        now=base + timedelta(hours=1),
        risk_config=_CFG,
    )
    assert decision.blocked is True
    assert decision.reason == "max_drawdown"
    assert decision.drawdown_pct == Decimal("0.375")
    async with pool.acquire() as conn:
        st = await select_kill_switch_state(conn, bot_id=bot_id)
    assert st is not None
    assert st.tripped is True
    assert st.trip_reason == "max_drawdown"
    assert st.cumulative_loss_usd == Decimal("75.0000")


async def test_sticky_latch_blocks_second_call_without_recompute(
    pool: asyncpg.Pool,
) -> None:
    bot_id = f"dd-{uuid.uuid4().hex[:8]}"
    base = datetime(2026, 5, 15, 9, 0, 0, tzinfo=UTC)
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        for i, pnl in enumerate((Decimal("100"), Decimal("-60"))):
            await _seed_closed_paper_trade(
                conn,
                bot_id=bot_id,
                realized_pnl=pnl,
                closed_at=base + timedelta(minutes=10 * i),
            )
    now = base + timedelta(hours=1)
    first = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", bot_id),
        exchange_mode="paper",
        now=now,
        risk_config=_CFG,
    )
    assert first.blocked is True  # (100-40)/100 = 0.60 ≥ 0.20
    second = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", bot_id),
        exchange_mode="paper",
        now=now,
        risk_config=_CFG,
    )
    assert second.blocked is True
    assert second.reason == "max_drawdown"
    assert second.drawdown_pct is None  # sticky — recompute skipped


async def test_peak_le_zero_never_blocked(pool: asyncpg.Pool) -> None:
    """OQ-A guard vs real PG: a never-profitable bot (only losses) → not blocked."""
    bot_id = f"dd-{uuid.uuid4().hex[:8]}"
    base = datetime(2026, 5, 15, 9, 0, 0, tzinfo=UTC)
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        for i, pnl in enumerate((Decimal("-20"), Decimal("-30"), Decimal("-10"))):
            await _seed_closed_paper_trade(
                conn,
                bot_id=bot_id,
                realized_pnl=pnl,
                closed_at=base + timedelta(minutes=10 * i),
            )
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", bot_id),
        exchange_mode="paper",
        now=base + timedelta(hours=1),
        risk_config=_CFG,
    )
    assert decision.blocked is False  # peak = -20 ≤ 0 → guard, no division
