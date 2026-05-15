"""§N4 unit tests for :mod:`services.strategy_engine.app.concurrent_caps_gate` (T-524).

9 cases per plan T-524 §Test strategy:

1. both_caps_zero_short_circuits_no_db (WG#4 short-circuit pin)
2. per_bot_under_cap_passes
3. per_bot_at_cap_blocks (>= predicate, WG#5)
4. per_bot_over_cap_blocks (cap lowered post-open)
5. global_at_cap_blocks (per-bot disabled)
6. per_bot_disabled_global_enabled (per-bot query NOT issued — WG#4)
7. per_bot_binds_first_when_both_would_hit (deterministic precedence — WG#5)
8. paper_mode_counts_paper_trades (table dispatch)
9. live_mode_counts_trades (table dispatch)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from packages.scoring import RiskSection
from services.strategy_engine.app.concurrent_caps_gate import check_concurrent_caps

pytestmark = pytest.mark.asyncio


def _mock_pool(counts: list[int]) -> tuple[MagicMock, list[tuple[Any, ...]]]:
    """Stub asyncpg.Pool whose conn.fetchrow returns count(*) rows in order.

    ``counts`` is consumed FIFO per fetchrow call (per-bot query first, then
    global). Captures (sql, *args) per call so tests can pin which queries ran.
    """
    captured: list[tuple[Any, ...]] = []
    seq = iter(counts)

    async def _fetchrow(sql: str, *args: Any) -> tuple[int]:
        captured.append((sql, *args))
        return (next(seq),)

    conn = MagicMock()
    conn.fetchrow = _fetchrow

    class _AcquireCtx:
        async def __aenter__(self) -> MagicMock:
            return conn

        async def __aexit__(self, *_a: Any) -> None:
            return None

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx())
    return pool, captured


# 1
async def test_both_caps_zero_short_circuits_no_db() -> None:
    """WG#4: both knobs 0 → not blocked, pool.acquire NEVER called."""
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("must not acquire connection"))
    decision = await check_concurrent_caps(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(),  # all defaults 0
    )
    assert decision.blocked is False
    assert decision.reason is None
    pool.acquire.assert_not_called()


# 2
async def test_per_bot_under_cap_passes() -> None:
    pool, _ = _mock_pool([2])  # per-bot count = 2
    decision = await check_concurrent_caps(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_per_bot=3),
    )
    assert decision.blocked is False


# 3
async def test_per_bot_at_cap_blocks() -> None:
    """>= predicate: count == cap blocks (a new entry would exceed)."""
    pool, _ = _mock_pool([3])
    decision = await check_concurrent_caps(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_per_bot=3),
    )
    assert decision.blocked is True
    assert decision.reason == "max_open_trades_per_bot"
    assert decision.current_count == 3
    assert decision.cap_limit == 3


# 4
async def test_per_bot_over_cap_blocks() -> None:
    """Cap lowered after positions opened (count > cap) still blocks."""
    pool, _ = _mock_pool([5])
    decision = await check_concurrent_caps(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_per_bot=3),
    )
    assert decision.blocked is True
    assert decision.reason == "max_open_trades_per_bot"
    assert decision.current_count == 5


# 5
async def test_global_at_cap_blocks_per_bot_disabled() -> None:
    pool, _ = _mock_pool([10])  # only global query issued → first count is global
    decision = await check_concurrent_caps(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_global=10),
    )
    assert decision.blocked is True
    assert decision.reason == "max_open_trades_global"
    assert decision.current_count == 10
    assert decision.cap_limit == 10


# 6
async def test_per_bot_disabled_global_enabled_skips_per_bot_query() -> None:
    """WG#4: per-bot=0 → per-bot count query NOT issued; only global runs."""
    pool, captured = _mock_pool([3])  # single fetchrow = global
    decision = await check_concurrent_caps(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_global=5),
    )
    assert decision.blocked is False
    # Exactly ONE query issued (global), and it has NO bot_id bind.
    assert len(captured) == 1
    assert "WHERE status = 'open'" in captured[0][0]
    assert captured[0][1:] == ()  # no $1 bind → global


# 7
async def test_per_bot_binds_first_when_both_would_hit() -> None:
    """WG#5 deterministic precedence: per-bot checked before global."""
    # per-bot count=2 (cap 2 → hit). global query never reached.
    pool, captured = _mock_pool([2, 99])
    decision = await check_concurrent_caps(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_per_bot=2, max_open_trades_global=5),
    )
    assert decision.blocked is True
    assert decision.reason == "max_open_trades_per_bot"
    assert decision.current_count == 2
    # Only the per-bot query ran (global short-circuited by early return).
    assert len(captured) == 1
    assert "bot_id = $1" in captured[0][0]


# 8
async def test_paper_mode_counts_paper_trades() -> None:
    pool, captured = _mock_pool([0])
    await check_concurrent_caps(
        pool=pool,
        bot_id="beta",  # type: ignore[arg-type]
        exchange_mode="paper",
        risk_config=RiskSection(max_open_trades_per_bot=1),
    )
    assert "FROM paper_trades " in captured[0][0]
    assert captured[0][1:] == ("beta",)


# 9
async def test_live_mode_counts_trades() -> None:
    pool, captured = _mock_pool([0])
    await check_concurrent_caps(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        risk_config=RiskSection(max_open_trades_per_bot=1),
    )
    assert "FROM trades " in captured[0][0]
    assert captured[0][1:] == ("alpha",)
