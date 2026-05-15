"""§N4 unit tests for :mod:`services.strategy_engine.app.kill_switch_reconcile` (T-525a1).

WG#6 pins the 5-branch best-effort flow:

1. no state → kill_switch.reconcile_no_state info, no clear.
2. stale prior-day daily latch → clear_kill_switch called + reconcile_cleared
   info; updated_at == now.
3. same-UTC-day latch retained → NO clear + reconcile_latch_retained WARNING.
4. not tripped → kill_switch.reconcile_not_tripped info, no clear.
5. asyncpg error → reconcile_failed error logged + swallowed (does NOT raise).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from packages.db.queries.kill_switch import KillSwitchState
from services.strategy_engine.app.kill_switch_reconcile import (
    reconcile_kill_switch_on_startup,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 5, 16, 9, 0, 0, tzinfo=UTC)


def _pool_with_conn() -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()

    class _Ctx:
        async def __aenter__(self) -> MagicMock:
            return conn

        async def __aexit__(self, *_a: Any) -> None:
            return None

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    return pool, conn


async def test_no_state_logs_no_state_no_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _pool_with_conn()
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.select_kill_switch_state",
        AsyncMock(return_value=None),
    )
    clear = AsyncMock()
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.clear_kill_switch", clear
    )
    log = MagicMock()
    await reconcile_kill_switch_on_startup(
        pool=pool, bot_id="alpha", now_fn=lambda: _NOW, system_logger=log
    )
    clear.assert_not_called()
    assert log.info.call_args.args[0] == "kill_switch.reconcile_no_state"


async def test_stale_prior_day_latch_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _pool_with_conn()
    stale = KillSwitchState(
        bot_id="alpha",
        tripped=True,
        trip_reason="daily_loss_limit",
        tripped_at=datetime(2026, 5, 15, 10, 31, tzinfo=UTC),
        daily_anchor_date=date(2026, 5, 15),
        cumulative_loss_usd=Decimal("-105.0000"),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.select_kill_switch_state",
        AsyncMock(return_value=stale),
    )
    clear = AsyncMock()
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.clear_kill_switch", clear
    )
    log = MagicMock()
    await reconcile_kill_switch_on_startup(
        pool=pool, bot_id="alpha", now_fn=lambda: _NOW, system_logger=log
    )
    clear.assert_awaited_once()
    await_args = clear.await_args
    assert await_args is not None
    assert await_args.kwargs["bot_id"] == "alpha"
    assert await_args.kwargs["updated_at"] == _NOW
    assert log.info.call_args.args[0] == "kill_switch.reconcile_cleared_stale_daily_latch"


async def test_same_day_latch_retained_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _pool_with_conn()
    same_day = KillSwitchState(
        bot_id="alpha",
        tripped=True,
        trip_reason="daily_loss_limit",
        tripped_at=datetime(2026, 5, 16, 8, 0, tzinfo=UTC),
        daily_anchor_date=date(2026, 5, 16),
        cumulative_loss_usd=Decimal("-150.0000"),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.select_kill_switch_state",
        AsyncMock(return_value=same_day),
    )
    clear = AsyncMock()
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.clear_kill_switch", clear
    )
    log = MagicMock()
    await reconcile_kill_switch_on_startup(
        pool=pool, bot_id="alpha", now_fn=lambda: _NOW, system_logger=log
    )
    clear.assert_not_called()  # H-027: same-day latch survives restart
    assert log.warning.call_args.args[0] == "kill_switch.reconcile_latch_retained"


async def test_not_tripped_logs_not_tripped(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _ = _pool_with_conn()
    clear_state = KillSwitchState(
        bot_id="alpha",
        tripped=False,
        trip_reason=None,
        tripped_at=None,
        daily_anchor_date=None,
        cumulative_loss_usd=None,
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.select_kill_switch_state",
        AsyncMock(return_value=clear_state),
    )
    clear = AsyncMock()
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.clear_kill_switch", clear
    )
    log = MagicMock()
    await reconcile_kill_switch_on_startup(
        pool=pool, bot_id="alpha", now_fn=lambda: _NOW, system_logger=log
    )
    clear.assert_not_called()
    assert log.info.call_args.args[0] == "kill_switch.reconcile_not_tripped"


async def test_asyncpg_error_swallowed_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """WG#6: best-effort — asyncpg error logged + swallowed, NEVER raised."""
    pool, _ = _pool_with_conn()
    monkeypatch.setattr(
        "services.strategy_engine.app.kill_switch_reconcile.select_kill_switch_state",
        AsyncMock(side_effect=asyncpg.PostgresError("boom")),
    )
    log = MagicMock()
    # Must NOT raise.
    await reconcile_kill_switch_on_startup(
        pool=pool, bot_id="alpha", now_fn=lambda: _NOW, system_logger=log
    )
    assert log.error.call_args.args[0] == "kill_switch.reconcile_failed"
    assert log.error.call_args.kwargs["bot_id"] == "alpha"
