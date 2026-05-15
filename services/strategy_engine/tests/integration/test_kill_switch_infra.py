"""Integration test for T-525a1 kill-switch persistence substrate (H-027).

Reuses the shared ``services/strategy_engine/tests/integration/conftest.py``
``pool`` fixture (migrated DB incl. migration 0018). Bus-free. Verifies the
H-027 contract end-to-end against real PG:

* `test_kill_switch_latch_survives_simulated_restart_same_utc_day` — a tripped
  latch written, then `reconcile_kill_switch_on_startup` (simulating a restart)
  on the SAME UTC day, leaves the latch tripped (restart did NOT reset it).
* `test_reconcile_clears_stale_prior_utc_day_latch` — a tripped latch with
  `daily_anchor_date` = a prior UTC day is cleared by reconcile at boot.
* upsert idempotency round-trip — calling `upsert_kill_switch_trip` twice with
  the same args yields a single convergent row (no duplicate, no error).

Per WG#7 (HARD AC#9): MUST be executed locally with
``POSTGRES_TEST_DSN=postgresql://scalper:devpass@127.0.0.1:5432/postgres
uv run pytest services/strategy_engine/tests/integration/test_kill_switch_infra.py -v``
BEFORE git push (L-021 sub-gap — CI must not be the first execution surface).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from packages.db.queries.kill_switch import (
    select_kill_switch_state,
    upsert_kill_switch_trip,
)
from packages.observability import get_logger
from services.strategy_engine.app.kill_switch_reconcile import (
    reconcile_kill_switch_on_startup,
)

_LOG = get_logger("test-strategy-ks-integration", "system")

if TYPE_CHECKING:
    import asyncpg

pytestmark = pytest.mark.asyncio


async def _seed_bot(conn: object, bot_id: str) -> None:
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO bots (bot_id, display_name, created_at, status, "
        "exchange_mode, config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, 'active', 'paper', 'sha256:test', $4)",
        bot_id,
        f"T-525a1 ks smoke {bot_id}",
        datetime.now(UTC),
        datetime.now(UTC),
    )


async def test_kill_switch_latch_survives_simulated_restart_same_utc_day(
    pool: asyncpg.Pool,
) -> None:
    """H-027: same-UTC-day latch retained across a simulated restart."""
    bot_id = f"ks-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        await upsert_kill_switch_trip(
            conn,
            bot_id=bot_id,
            trip_reason="daily_loss_limit",
            tripped_at=now,
            daily_anchor_date=now.date(),  # today's UTC date
            cumulative_loss_usd=Decimal("-105.0000"),
        )

    # Simulate a strategy-engine restart on the SAME UTC day.
    await reconcile_kill_switch_on_startup(
        pool=pool,
        bot_id=bot_id,
        now_fn=lambda: now,
        system_logger=_LOG,
    )

    async with pool.acquire() as conn:
        st = await select_kill_switch_state(conn, bot_id=bot_id)
    assert st is not None
    assert st.tripped is True  # H-027: restart did NOT reset the stop
    assert st.trip_reason == "daily_loss_limit"
    assert st.cumulative_loss_usd == Decimal("-105.0000")


async def test_reconcile_clears_stale_prior_utc_day_latch(
    pool: asyncpg.Pool,
) -> None:
    """A daily latch anchored to a prior UTC day is cleared at boot."""
    bot_id = f"ks-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    yesterday = (now - timedelta(days=1)).date()
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        await upsert_kill_switch_trip(
            conn,
            bot_id=bot_id,
            trip_reason="daily_loss_limit",
            tripped_at=now - timedelta(days=1),
            daily_anchor_date=yesterday,
            cumulative_loss_usd=Decimal("-200.0000"),
        )

    await reconcile_kill_switch_on_startup(
        pool=pool,
        bot_id=bot_id,
        now_fn=lambda: now,
        system_logger=_LOG,
    )

    async with pool.acquire() as conn:
        st = await select_kill_switch_state(conn, bot_id=bot_id)
    assert st is not None
    assert st.tripped is False  # stale prior-day daily latch cleared
    assert st.trip_reason is None


async def test_upsert_idempotency_round_trip_single_row(
    pool: asyncpg.Pool,
) -> None:
    """@idempotent: re-applying the same trip is convergent (one row, no error)."""
    bot_id = f"ks-{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    anchor: date = now.date()
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        for _ in range(2):
            await upsert_kill_switch_trip(
                conn,
                bot_id=bot_id,
                trip_reason="daily_loss_limit",
                tripped_at=now,
                daily_anchor_date=anchor,
                cumulative_loss_usd=Decimal("-105.0000"),
            )
        count = await conn.fetchval(
            "SELECT count(*) FROM bot_kill_switch_state WHERE bot_id = $1", bot_id
        )
    assert count == 1  # convergent — no duplicate row
