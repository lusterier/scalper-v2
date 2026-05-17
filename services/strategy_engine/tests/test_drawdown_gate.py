"""§N4 unit tests for :mod:`services.strategy_engine.app.drawdown_gate` (T-525b).

10 cases per plan T-525b §Test strategy:

1. disabled_zero_pct_short_circuits_no_db
2. peak_le_zero_never_tripped (OQ-A guard — peak=0 AND negative-peak)
3. under_drawdown_not_blocked
4. at_drawdown_trips (boundary 0.20 >= 0.20)
5. over_drawdown_trips
6. current_negative_drawdown_exceeds_one
7. already_latched_blocks_without_recompute (L-017 bilateral pin)
8. reason_agnostic_blocks_on_daily_loss_latch
9. exact_decimal_division_hand_value ((3-1)/3 == 0.666...7)
10. paper_mode / live_mode dispatch
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.kill_switch import KillSwitchState
from packages.scoring import RiskSection
from services.strategy_engine.app.drawdown_gate import check_max_drawdown

if TYPE_CHECKING:
    from packages.core import BotId

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 5, 15, 10, 31, 0, tzinfo=UTC)
_CFG = RiskSection(max_drawdown_pct=Decimal("0.20"))


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
        cumulative_loss_usd=Decimal("75.0000") if tripped else None,
    )


def _patch(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> dict[str, AsyncMock]:
    """Patch the gate's substrate calls; return the spies."""
    spies = {
        "select": AsyncMock(return_value=kw.get("state")),
        "peak_current": AsyncMock(
            return_value=kw.get("peak_current", (Decimal("0"), Decimal("0")))
        ),
        "upsert": AsyncMock(),
        "clear": AsyncMock(),
    }
    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.select_kill_switch_state", spies["select"]
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.select_pnl_peak_and_current",
        spies["peak_current"],
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.upsert_kill_switch_trip", spies["upsert"]
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.clear_kill_switch", spies["clear"]
    )
    return spies


# 1
async def test_disabled_zero_pct_short_circuits_no_db() -> None:
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("must not acquire connection"))
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(),  # max_drawdown_pct default Decimal("0")
    )
    assert decision.blocked is False
    pool.acquire.assert_not_called()


# 2
@pytest.mark.parametrize("peak", [Decimal("0"), Decimal("-10")])
async def test_peak_le_zero_never_tripped(monkeypatch: pytest.MonkeyPatch, peak: Decimal) -> None:
    """OQ-A guard: peak<=0 → not blocked; select_pnl_peak_and_current IS
    called but the division is NOT attempted (no /0, no /negative)."""
    pool, _ = _mock_pool()
    spies = _patch(monkeypatch, state=None, peak_current=(peak, Decimal("-80")))
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=_CFG,
    )
    assert decision.blocked is False
    spies["peak_current"].assert_awaited_once()  # called
    spies["upsert"].assert_not_called()  # division/trip not attempted


# 3
async def test_under_drawdown_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    spies = _patch(monkeypatch, state=None, peak_current=(Decimal("100"), Decimal("90")))
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=_CFG,
    )
    assert decision.blocked is False  # (100-90)/100 = 0.10 < 0.20
    spies["upsert"].assert_not_called()


# 4
async def test_at_drawdown_trips_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    spies = _patch(monkeypatch, state=None, peak_current=(Decimal("100"), Decimal("80")))
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=_CFG,
    )
    assert decision.blocked is True  # (100-80)/100 = 0.20 >= 0.20 (boundary trips)
    assert decision.reason == "max_drawdown"
    assert decision.drawdown_pct == Decimal("0.20")
    assert decision.limit_pct == Decimal("0.20")
    spies["upsert"].assert_awaited_once()
    up = spies["upsert"].await_args
    assert up is not None
    assert up.kwargs["trip_reason"] == "max_drawdown"
    assert up.kwargs["cumulative_loss_usd"] == Decimal("80")
    assert up.kwargs["daily_anchor_date"] == _NOW.date()


# 5
async def test_over_drawdown_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    _patch(monkeypatch, state=None, peak_current=(Decimal("100"), Decimal("50")))
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=_CFG,
    )
    assert decision.blocked is True  # 0.50 >= 0.20
    assert decision.drawdown_pct == Decimal("0.5")


# 6
async def test_current_negative_drawdown_exceeds_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """current<0<peak → give-back > 100% (legitimate; no le bound on knob)."""
    pool, _ = _mock_pool()
    _patch(monkeypatch, state=None, peak_current=(Decimal("100"), Decimal("-50")))
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=_CFG,
    )
    assert decision.blocked is True
    assert decision.drawdown_pct == Decimal("1.5")  # (100-(-50))/100


# 7
async def test_already_latched_blocks_without_recompute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-017 bilateral pin: sticky max_drawdown latch → blocked,
    select_pnl_peak_and_current NOT called."""
    pool, _ = _mock_pool()
    spies = _patch(
        monkeypatch,
        state=_state(tripped=True, reason="max_drawdown", anchor=date(2026, 5, 15)),
    )
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=_CFG,
    )
    assert decision.blocked is True
    assert decision.reason == "max_drawdown"
    assert decision.drawdown_pct is None  # recompute skipped
    spies["peak_current"].assert_not_called()


# 8
async def test_reason_agnostic_blocks_on_daily_loss_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-stale daily_loss_limit latch cross-blocks the drawdown gate too
    (reason-agnostic; symmetric with T-525a2 #9)."""
    pool, _ = _mock_pool()
    spies = _patch(
        monkeypatch,
        state=_state(tripped=True, reason="daily_loss_limit", anchor=date(2026, 5, 15)),
    )
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=_CFG,
    )
    assert decision.blocked is True
    assert decision.reason == "daily_loss_limit"  # state's reason, reason-agnostic
    spies["peak_current"].assert_not_called()


# 9
async def test_exact_decimal_division_hand_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins exact CPython 28-digit Decimal division: (3-1)/3."""
    pool, _ = _mock_pool()
    _patch(monkeypatch, state=None, peak_current=(Decimal("3"), Decimal("1")))
    decision = await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=RiskSection(max_drawdown_pct=Decimal("0.66")),
    )
    assert decision.drawdown_pct == Decimal("0.6666666666666666666666666667")
    assert decision.blocked is True  # 0.666...7 >= 0.66


# 10
async def test_paper_mode_queries_paper_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    captured: dict[str, Any] = {}

    async def _pc(_conn: Any, *, table_name: str, bot_id: str) -> tuple[Decimal, Decimal]:
        captured["table_name"] = table_name
        return (Decimal("0"), Decimal("0"))

    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.select_pnl_peak_and_current", _pc
    )
    await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="paper",
        now=_NOW,
        risk_config=_CFG,
    )
    assert captured["table_name"] == "paper_trades"


async def test_live_mode_queries_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _mock_pool()
    captured: dict[str, Any] = {}

    async def _pc(_conn: Any, *, table_name: str, bot_id: str) -> tuple[Decimal, Decimal]:
        captured["table_name"] = table_name
        return (Decimal("0"), Decimal("0"))

    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.select_pnl_peak_and_current", _pc
    )
    await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="live",
        now=_NOW,
        risk_config=_CFG,
    )
    assert captured["table_name"] == "trades"


async def test_demo_mode_queries_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-549a §N4 pin: demo is a real Bybit demo-trading account → real `trades`
    table (NOT paper_trades). Guards the gate dispatch tuple regression."""
    pool, _ = _mock_pool()
    captured: dict[str, Any] = {}

    async def _pc(_conn: Any, *, table_name: str, bot_id: str) -> tuple[Decimal, Decimal]:
        captured["table_name"] = table_name
        return (Decimal("0"), Decimal("0"))

    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.drawdown_gate.select_pnl_peak_and_current", _pc
    )
    await check_max_drawdown(
        pool=pool,
        bot_id=cast("BotId", "alpha"),
        exchange_mode="demo",
        now=_NOW,
        risk_config=_CFG,
    )
    assert captured["table_name"] == "trades"
