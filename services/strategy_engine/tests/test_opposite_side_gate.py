"""§N4 unit tests for :mod:`services.strategy_engine.app.opposite_side_gate` (T-542).

H-005 opposite-side guard — the `## Hand verification` truth table from
`docs/plans/T-542.md`. `signal_side = _ACTION_TO_SIDE[action]` (LONG→buy /
SHORT→sell; CLOSE never reaches the gate — consumer returns earlier). Block
iff an open position row exists for ``(bot_id, symbol)`` AND
``open_side != signal_side`` (opposite). Same side = pyramid/add (allow);
no open row = allow; ``block_opposite_side=False`` = short-circuit, NO DB hit.

Cases:
1. no_open_position_long_allows               (no row → inactive)
2. no_open_position_short_allows              (no row → inactive)
3. same_side_buy_open_long_allows             (buy==buy, pyramid → inactive)
4. blocks_opposite_open_side                  (buy open + SHORT → BLOCK)  ← §20 H-005 pin
5. same_side_sell_open_short_allows           (sell==sell, add → inactive)
6. blocks_opposite_sell_open_long             (sell open + LONG → BLOCK)
7. disabled_short_circuits_no_db_hit          (block_opposite_side=False, NO acquire)
8. paper_mode_queries_paper_position_state +  live_mode_queries_position_state (dispatch)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from packages.scoring import RiskSection
from services.strategy_engine.app.opposite_side_gate import check_opposite_side

pytestmark = pytest.mark.asyncio


def _mock_pool(open_side: str | None) -> tuple[MagicMock, list[tuple[Any, ...]]]:
    """Stub asyncpg.Pool: ``conn.fetchrow`` → ``{"side": open_side}`` or ``None``.

    Captures ``(sql, *args)`` so the table-dispatch + bind args can be pinned.
    """
    captured_calls: list[tuple[Any, ...]] = []

    async def _fetchrow(sql: str, *args: Any) -> dict[str, Any] | None:
        captured_calls.append((sql, *args))
        return None if open_side is None else {"side": open_side}

    conn = MagicMock()
    conn.fetchrow = _fetchrow

    class _AcquireCtx:
        async def __aenter__(self) -> MagicMock:
            return conn

        async def __aexit__(self, *_args: Any) -> None:
            return None

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx())
    return pool, captured_calls


# 1
async def test_no_open_position_long_allows() -> None:
    pool, _ = _mock_pool(None)
    decision = await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        symbol="BTCUSDT",
        signal_side="buy",
        risk_config=RiskSection(),
    )
    assert decision.active is False
    assert decision.reason is None
    assert decision.open_side is None
    assert decision.signal_side == "buy"


# 2
async def test_no_open_position_short_allows() -> None:
    pool, _ = _mock_pool(None)
    decision = await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        symbol="BTCUSDT",
        signal_side="sell",
        risk_config=RiskSection(),
    )
    assert decision.active is False


# 3
async def test_same_side_buy_open_long_allows() -> None:
    """Open LONG (buy) + LONG signal (buy) → same side, pyramid/add, NOT opposite."""
    pool, _ = _mock_pool("buy")
    decision = await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        symbol="BTCUSDT",
        signal_side="buy",
        risk_config=RiskSection(),
    )
    assert decision.active is False
    assert decision.open_side == "buy"
    assert decision.signal_side == "buy"


# 4 — the §20 H-005 canonical pin
async def test_blocks_opposite_open_side() -> None:
    """Open LONG (buy) + SHORT signal (sell) → opposite → BLOCK."""
    pool, _ = _mock_pool("buy")
    decision = await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        symbol="BTCUSDT",
        signal_side="sell",
        risk_config=RiskSection(),
    )
    assert decision.active is True
    assert decision.reason == "opposite_side_open"
    assert decision.open_side == "buy"
    assert decision.signal_side == "sell"


# 5
async def test_same_side_sell_open_short_allows() -> None:
    pool, _ = _mock_pool("sell")
    decision = await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        symbol="BTCUSDT",
        signal_side="sell",
        risk_config=RiskSection(),
    )
    assert decision.active is False


# 6
async def test_blocks_opposite_sell_open_long() -> None:
    """Open SHORT (sell) + LONG signal (buy) → opposite → BLOCK."""
    pool, _ = _mock_pool("sell")
    decision = await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        symbol="BTCUSDT",
        signal_side="buy",
        risk_config=RiskSection(),
    )
    assert decision.active is True
    assert decision.reason == "opposite_side_open"
    assert decision.open_side == "sell"
    assert decision.signal_side == "buy"


# 7 — WG#2 short-circuit pin
async def test_disabled_short_circuits_no_db_hit() -> None:
    """block_opposite_side=False → _INACTIVE BEFORE any pool.acquire (no DB hit)."""
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("must not acquire connection"))
    decision = await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        symbol="BTCUSDT",
        signal_side="sell",
        risk_config=RiskSection(block_opposite_side=False),
    )
    assert decision.active is False
    assert decision.reason is None
    pool.acquire.assert_not_called()


# 8 — table dispatch by exchange_mode
async def test_paper_mode_queries_paper_position_state() -> None:
    pool, captured = _mock_pool(None)
    await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        symbol="BTCUSDT",
        signal_side="buy",
        risk_config=RiskSection(),
    )
    assert len(captured) == 1
    sql = captured[0][0]
    assert "paper_position_state" in sql
    assert captured[0][1:] == ("alpha", "BTCUSDT")


async def test_live_mode_queries_position_state() -> None:
    pool, captured = _mock_pool(None)
    await check_opposite_side(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        symbol="BTCUSDT",
        signal_side="buy",
        risk_config=RiskSection(),
    )
    assert len(captured) == 1
    sql = captured[0][0]
    assert "position_state" in sql
    assert "paper_position_state" not in sql
