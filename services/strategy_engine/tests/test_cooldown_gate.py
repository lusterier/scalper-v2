"""§N4 unit tests for :mod:`services.strategy_engine.app.cooldown_gate` (T-526).

11 cases per plan T-526 §Test strategy:

1. no_closed_trades_returns_inactive
2. all_zero_config_returns_inactive_no_db_hit (WG#4 short-circuit pin)
3. single_recent_loss_within_loss_cooldown
4. single_loss_outside_loss_cooldown
5. streak_count_below_threshold
6. streak_count_meets_threshold_within_streak_cooldown
7. streak_resets_on_win
8. zero_pnl_resets_streak (OQ-2=A strict ``< 0``)
9. both_cooldowns_active_picks_max (OQ-4=A)
10. paper_mode_queries_paper_trades + live_mode_queries_trades (table_name dispatch)
11. streak_n_zero_disables_streak_cooldown_regardless_of_streak_minutes (WG#4 pin)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from packages.db.queries.trades import ClosedTradeRow
from packages.scoring import RiskSection
from services.strategy_engine.app.cooldown_gate import check_cooldown

_T_NOW = datetime(2026, 5, 15, 10, 5, 0, tzinfo=UTC)
_T_LOSS_0 = datetime(2026, 5, 15, 10, 0, 0, tzinfo=UTC)
_T_LOSS_1 = datetime(2026, 5, 15, 9, 50, 0, tzinfo=UTC)
_T_LOSS_2 = datetime(2026, 5, 15, 9, 30, 0, tzinfo=UTC)
_T_WIN_OLD = datetime(2026, 5, 15, 9, 0, 0, tzinfo=UTC)


pytestmark = pytest.mark.asyncio


def _mock_pool(rows: list[ClosedTradeRow]) -> tuple[MagicMock, list[tuple[Any, ...]]]:
    """Stub asyncpg.Pool returning ``rows`` from ``conn.fetch``. Captures fetch args."""
    captured_calls: list[tuple[Any, ...]] = []

    async def _fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
        captured_calls.append((sql, *args))
        return [{"realized_pnl": r.realized_pnl, "closed_at": r.closed_at} for r in rows]

    conn = MagicMock()
    conn.fetch = _fetch

    class _AcquireCtx:
        async def __aenter__(self) -> MagicMock:
            return conn

        async def __aexit__(self, *_args: Any) -> None:
            return None

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx())
    return pool, captured_calls


# 1
async def test_no_closed_trades_returns_inactive() -> None:
    pool, _ = _mock_pool([])
    cfg = RiskSection(
        cooldown_after_loss_minutes=10,
        cooldown_after_streak_n_losses=3,
        cooldown_after_streak_n_losses_minutes=60,
    )
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert decision.active is False
    assert decision.reason is None
    assert decision.cooldown_until is None
    assert decision.streak_count == 0
    assert decision.last_loss_at is None


# 2
async def test_all_zero_config_returns_inactive_no_db_hit() -> None:
    """WG#4 short-circuit pin: zero-config skips SELECT entirely."""
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("must not acquire connection"))
    cfg = RiskSection()  # all defaults zero
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert decision.active is False
    pool.acquire.assert_not_called()


# 3
async def test_single_recent_loss_within_loss_cooldown() -> None:
    pool, _ = _mock_pool([ClosedTradeRow(realized_pnl=Decimal("-5.00"), closed_at=_T_LOSS_0)])
    cfg = RiskSection(cooldown_after_loss_minutes=10)
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,  # 10:05, within 10min of 10:00 loss
        risk_config=cfg,
    )
    assert decision.active is True
    assert decision.reason == "cooldown_after_loss"
    assert decision.cooldown_until == _T_LOSS_0 + timedelta(minutes=10)
    assert decision.streak_count == 1
    assert decision.last_loss_at == _T_LOSS_0


# 4
async def test_single_loss_outside_loss_cooldown() -> None:
    pool, _ = _mock_pool([ClosedTradeRow(realized_pnl=Decimal("-5.00"), closed_at=_T_LOSS_0)])
    cfg = RiskSection(cooldown_after_loss_minutes=10)
    now = _T_LOSS_0 + timedelta(minutes=11)  # past cooldown
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=now,
        risk_config=cfg,
    )
    assert decision.active is False
    assert decision.streak_count == 1
    assert decision.last_loss_at == _T_LOSS_0


# 5
async def test_streak_count_below_threshold() -> None:
    pool, _ = _mock_pool(
        [
            ClosedTradeRow(realized_pnl=Decimal("-1.00"), closed_at=_T_LOSS_0),
            ClosedTradeRow(realized_pnl=Decimal("-2.00"), closed_at=_T_LOSS_1),
            ClosedTradeRow(realized_pnl=Decimal("+10.00"), closed_at=_T_WIN_OLD),
        ]
    )
    cfg = RiskSection(
        cooldown_after_streak_n_losses=3,
        cooldown_after_streak_n_losses_minutes=60,
    )  # loss_minutes=0 disables single-loss knob
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert decision.active is False
    assert decision.streak_count == 2


# 6
async def test_streak_count_meets_threshold_within_streak_cooldown() -> None:
    pool, _ = _mock_pool(
        [
            ClosedTradeRow(realized_pnl=Decimal("-1.00"), closed_at=_T_LOSS_0),
            ClosedTradeRow(realized_pnl=Decimal("-2.00"), closed_at=_T_LOSS_1),
            ClosedTradeRow(realized_pnl=Decimal("-3.00"), closed_at=_T_LOSS_2),
        ]
    )
    cfg = RiskSection(
        cooldown_after_streak_n_losses=3,
        cooldown_after_streak_n_losses_minutes=60,
    )
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert decision.active is True
    assert decision.reason == "cooldown_after_streak"
    assert decision.cooldown_until == _T_LOSS_0 + timedelta(minutes=60)
    assert decision.streak_count == 3


# 7
async def test_streak_resets_on_win() -> None:
    """Win in middle of window stops streak walk."""
    pool, _ = _mock_pool(
        [
            ClosedTradeRow(realized_pnl=Decimal("-1.00"), closed_at=_T_LOSS_0),
            ClosedTradeRow(realized_pnl=Decimal("-2.00"), closed_at=_T_LOSS_1),
            ClosedTradeRow(realized_pnl=Decimal("+5.00"), closed_at=_T_LOSS_2),
            ClosedTradeRow(realized_pnl=Decimal("-1.00"), closed_at=_T_WIN_OLD),
        ]
    )
    cfg = RiskSection(
        cooldown_after_streak_n_losses=3,
        cooldown_after_streak_n_losses_minutes=60,
    )
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert decision.active is False
    assert decision.streak_count == 2  # walk stops at the +5.00 row


# 8
async def test_zero_pnl_resets_streak() -> None:
    """OQ-2=A strict ``< 0``: realized_pnl=0 is NOT a loss → counter stops there."""
    pool, _ = _mock_pool(
        [
            ClosedTradeRow(realized_pnl=Decimal("-1.00"), closed_at=_T_LOSS_0),
            ClosedTradeRow(realized_pnl=Decimal("0.00"), closed_at=_T_LOSS_1),
            ClosedTradeRow(realized_pnl=Decimal("-3.00"), closed_at=_T_LOSS_2),
        ]
    )
    cfg = RiskSection(
        cooldown_after_streak_n_losses=3,
        cooldown_after_streak_n_losses_minutes=60,
    )
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert decision.active is False
    assert decision.streak_count == 1  # walk stops at the 0.00 row


# 9
async def test_both_cooldowns_active_picks_max() -> None:
    """OQ-4=A: when both binding, cooldown_until = max(loss_until, streak_until)."""
    pool, _ = _mock_pool(
        [
            ClosedTradeRow(realized_pnl=Decimal("-1.00"), closed_at=_T_LOSS_0),
            ClosedTradeRow(realized_pnl=Decimal("-2.00"), closed_at=_T_LOSS_1),
            ClosedTradeRow(realized_pnl=Decimal("-3.00"), closed_at=_T_LOSS_2),
        ]
    )
    cfg = RiskSection(
        cooldown_after_loss_minutes=10,  # → 10:10
        cooldown_after_streak_n_losses=3,
        cooldown_after_streak_n_losses_minutes=60,  # → 11:00
    )
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,  # 10:05
        risk_config=cfg,
    )
    assert decision.active is True
    assert decision.reason == "cooldown_after_loss_and_streak"
    # 11:00 (streak) > 10:10 (loss); max wins
    assert decision.cooldown_until == _T_LOSS_0 + timedelta(minutes=60)


# 10
async def test_paper_mode_queries_paper_trades_table() -> None:
    pool, captured = _mock_pool([])
    cfg = RiskSection(cooldown_after_loss_minutes=10)
    await check_cooldown(
        pool=pool,
        bot_id="beta",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert len(captured) == 1
    sql = captured[0][0]
    assert "FROM paper_trades " in sql
    # bot_id + limit are the only binds
    assert captured[0][1:] == ("beta", 1)


async def test_live_mode_queries_trades_table() -> None:
    pool, captured = _mock_pool([])
    cfg = RiskSection(cooldown_after_loss_minutes=10)
    await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert len(captured) == 1
    assert "FROM trades " in captured[0][0]
    assert captured[0][1:] == ("alpha", 1)


async def test_demo_mode_queries_trades_table() -> None:
    """T-549a §N4 pin: demo (real Bybit demo-trading account) → real `trades`,
    NOT paper_trades — else the cooldown streak walk reads empty paper data."""
    pool, captured = _mock_pool([])
    cfg = RiskSection(cooldown_after_loss_minutes=10)
    await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="demo",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert len(captured) == 1
    assert "FROM trades " in captured[0][0]
    assert captured[0][1:] == ("alpha", 1)


# 11
async def test_streak_n_zero_disables_streak_cooldown_regardless_of_streak_minutes() -> None:
    """WG#4 pin: streak_n=0 disables streak knob even when streak_minutes>0.

    Symmetric pin: streak_minutes=0 also disables (covered implicitly by case 3 +
    case 6 mirror; the canonical case is streak_n=0).
    """
    pool, captured = _mock_pool(
        [
            ClosedTradeRow(realized_pnl=Decimal("-1.00"), closed_at=_T_LOSS_0),
            ClosedTradeRow(realized_pnl=Decimal("-2.00"), closed_at=_T_LOSS_1),
        ]
    )
    cfg = RiskSection(
        cooldown_after_streak_n_losses=0,
        cooldown_after_streak_n_losses_minutes=60,  # absurd; should be ignored
    )
    # loss_minutes=0 too → both knobs disabled → short-circuit (no DB hit)
    decision = await check_cooldown(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_T_NOW,
        risk_config=cfg,
    )
    assert decision.active is False
    # Short-circuit pin: no SELECT issued
    assert captured == []
