"""§N4 unit tests for :mod:`services.strategy_engine.app.loss_limit_gate` (T-525a2).

10 cases per plan T-525a2 §Test strategy:

1. disabled_zero_limit_short_circuits_no_db (WG#1 short-circuit)
2. under_limit_not_blocked
3. at_limit_trips_and_latches (WG#9 boundary)
4. over_limit_trips
5. already_latched_same_day_blocks_without_recompute (WG#4 L-017 bilateral pin)
6. latched_stale_prior_day_clears_then_recomputes (WG#6 day-rollover)
7. sticky_latch_recovering_win_still_blocks (WG#4)
8. boundary_exactly_at_limit_blocks (WG#9)
9. reason_agnostic_blocks_on_max_drawdown_latch (WG#5 operator OQ 2026-05-15)
10. paper_mode_sums_paper_trades / live_mode_sums_trades (table dispatch)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.kill_switch import KillSwitchState
from packages.scoring import RiskSection
from services.strategy_engine.app.loss_limit_gate import check_daily_loss_limit

pytestmark = pytest.mark.asyncio

# 2026-05-15 10:31 UTC — same day as the latch anchor below.
_NOW = datetime(2026, 5, 15, 10, 31, 0, tzinfo=UTC)


def _mock_pool() -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()

    class _Ctx:
        async def __aenter__(self) -> MagicMock:
            return conn

        async def __aexit__(self, *_a: Any) -> None:
            return None

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    return pool, conn


def _state(*, tripped: bool, reason: str | None, anchor: date | None) -> KillSwitchState:
    return KillSwitchState(
        bot_id="alpha",
        tripped=tripped,
        trip_reason=reason,
        tripped_at=_NOW if tripped else None,
        daily_anchor_date=anchor,
        cumulative_loss_usd=Decimal("-105.0000") if tripped else None,
    )


# 1
async def test_disabled_zero_limit_short_circuits_no_db() -> None:
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("must not acquire connection"))
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(),  # daily_loss_limit_usd default Decimal("0")
    )
    assert decision.blocked is False
    pool.acquire.assert_not_called()


# 2
async def test_under_limit_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since",
        AsyncMock(return_value=Decimal("-50.0000")),
    )
    upsert = AsyncMock()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.upsert_kill_switch_trip", upsert
    )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert decision.blocked is False
    upsert.assert_not_called()


# 3
async def test_at_limit_trips_and_latches(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since",
        AsyncMock(return_value=Decimal("-100.0000")),
    )
    upsert = AsyncMock()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.upsert_kill_switch_trip", upsert
    )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert decision.blocked is True
    assert decision.reason == "daily_loss_limit"
    assert decision.cumulative_loss_usd == Decimal("-100.0000")
    assert decision.limit_usd == Decimal("100")
    upsert.assert_awaited_once()
    upsert_args = upsert.await_args
    assert upsert_args is not None
    kw = upsert_args.kwargs
    assert kw["trip_reason"] == "daily_loss_limit"
    assert kw["cumulative_loss_usd"] == Decimal("-100.0000")
    assert kw["daily_anchor_date"] == _NOW.date()


# 4
async def test_over_limit_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since",
        AsyncMock(return_value=Decimal("-150.0000")),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.upsert_kill_switch_trip",
        AsyncMock(),
    )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert decision.blocked is True
    assert decision.reason == "daily_loss_limit"


# 5
async def test_already_latched_same_day_blocks_without_recompute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#4 L-017 bilateral pin: sticky latch → blocked, sum NOT called."""
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(
            return_value=_state(tripped=True, reason="daily_loss_limit", anchor=date(2026, 5, 15))
        ),
    )
    sum_spy = AsyncMock(return_value=Decimal("0"))
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since", sum_spy
    )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert decision.blocked is True
    assert decision.reason == "daily_loss_limit"
    sum_spy.assert_not_called()  # L-017: should-NOT-be-called side pinned


# 6
async def test_latched_stale_prior_day_clears_then_recomputes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#6 day-rollover: stale prior-day latch cleared, then fresh under-limit → not blocked."""
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(
            return_value=_state(tripped=True, reason="daily_loss_limit", anchor=date(2026, 5, 14))
        ),
    )
    clear = AsyncMock()
    monkeypatch.setattr("services.strategy_engine.app.loss_limit_gate.clear_kill_switch", clear)
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since",
        AsyncMock(return_value=Decimal("-10.0000")),
    )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    clear.assert_awaited_once()
    clear_args = clear.await_args
    assert clear_args is not None
    assert clear_args.kwargs["updated_at"] == _NOW
    assert decision.blocked is False  # fresh day, -10 > -100


# 7
async def test_sticky_latch_recovering_win_still_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A +win that would bring the recomputed sum above -limit must NOT un-trip."""
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(
            return_value=_state(tripped=True, reason="daily_loss_limit", anchor=date(2026, 5, 15))
        ),
    )
    # If recompute were (wrongly) run it would see -45 > -100 → not blocked.
    sum_spy = AsyncMock(return_value=Decimal("-45.0000"))
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since", sum_spy
    )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert decision.blocked is True  # sticky — win does not un-trip
    sum_spy.assert_not_called()


# 8
async def test_boundary_exactly_at_limit_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """WG#9: Decimal('-100.0000') <= -Decimal('100') → True → trip."""
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since",
        AsyncMock(return_value=Decimal("-100.0000")),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.upsert_kill_switch_trip",
        AsyncMock(),
    )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert decision.blocked is True


# 9
async def test_reason_agnostic_blocks_on_max_drawdown_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#5 operator OQ: a non-stale max_drawdown latch (T-525b forward-compat)
    blocks the daily-loss gate; reason = state.trip_reason, NOT 'daily_loss_limit'."""
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(
            return_value=_state(tripped=True, reason="max_drawdown", anchor=date(2026, 5, 15))
        ),
    )
    sum_spy = AsyncMock(return_value=Decimal("0"))
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since", sum_spy
    )
    decision = await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert decision.blocked is True
    assert decision.reason == "max_drawdown"  # state's reason, reason-agnostic
    sum_spy.assert_not_called()  # is_stale_daily_latch False for max_drawdown → sticky


# 10
async def test_paper_mode_sums_paper_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    captured: dict[str, Any] = {}

    async def _sum(_conn: Any, *, table_name: str, bot_id: str, since: Any) -> Decimal:
        captured["table_name"] = table_name
        return Decimal("0")

    monkeypatch.setattr("services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since", _sum)
    await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert captured["table_name"] == "paper_trades"


async def test_live_mode_sums_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    monkeypatch.setattr(
        "services.strategy_engine.app.loss_limit_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    captured: dict[str, Any] = {}

    async def _sum(_conn: Any, *, table_name: str, bot_id: str, since: Any) -> Decimal:
        captured["table_name"] = table_name
        return Decimal("0")

    monkeypatch.setattr("services.strategy_engine.app.loss_limit_gate.sum_realized_pnl_since", _sum)
    await check_daily_loss_limit(
        pool=pool,
        bot_id="alpha",  # type: ignore[arg-type]
        exchange_mode="live",
        now=_NOW,
        risk_config=RiskSection(daily_loss_limit_usd=Decimal("100")),
    )
    assert captured["table_name"] == "trades"
