"""§N4 unit tests for :mod:`services.execution.app.lifecycle` (T-217a).

Mock-based: pool + bus.kv_get + asyncpg conn ctx + select_position_state +
update_position_state_monitor_tick patched on dispatcher_mod-symmetric module.
Validates per-tick MFE/MAE/best_price/running_pnl computation, stale-tick
WARN at threshold, KV decode robustness, graceful self-cancel on
position_state DELETE, and CancelledError propagation.

H-018-symmetric: composite-PK update on position_state writes only the 4
monitor-only columns (T-217a fields); does not touch fill-flow columns
(T-218b's ``remaining_qty``/``sl_type``).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core import BotId, TradeLifecycleState
from packages.db.queries.execution import PositionStateRow
from packages.exchange.errors import (
    AuthError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
    UnknownState,
)
from packages.exchange.types import Position
from services.execution.app import lifecycle as lifecycle_mod
from services.execution.app.lifecycle import (
    _detect_sl_overwrite,
    _update_best_price,
    _update_mfe_mae,
    run_position_monitor_for_trade,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _ps_row(
    *,
    side: str = "buy",
    qty: Decimal = Decimal("10"),
    remaining_qty: Decimal = Decimal("10"),
    sl_type: str | None = "protective",
    trade_id: int = 1,
    bot_id: str = "alpha",
    symbol: str = "BTCUSDT",
    entry_price: Decimal = Decimal("100"),
    sl_price: Decimal | None = Decimal("95"),
    tp_price: Decimal | None = Decimal("110"),
    best_price: Decimal | None = None,
    mfe_price: Decimal | None = None,
    mae_price: Decimal | None = None,
    running_pnl: Decimal = Decimal("0"),
) -> PositionStateRow:
    return PositionStateRow(
        bot_id=bot_id,
        symbol=symbol,
        trade_id=trade_id,
        side=side,  # type: ignore[arg-type]
        entry_price=entry_price,
        qty=qty,
        remaining_qty=remaining_qty,
        sl_price=sl_price,
        tp_price=tp_price,
        sl_type=sl_type,
        best_price=best_price,
        mfe_price=mfe_price,
        mae_price=mae_price,
        running_pnl=running_pnl,
    )


class _FakeConn:
    pass


def _build_pool() -> MagicMock:
    conn = _FakeConn()
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    pool.acquire = _acquire
    return pool


@pytest.fixture
def patched_queries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    mocks: dict[str, Any] = {
        "select_position_state": AsyncMock(return_value=_ps_row()),
        "update_position_state_monitor_tick": AsyncMock(return_value=None),
        "select_trade_fsm_params": AsyncMock(
            return_value={
                "be_trigger": Decimal("0.005"),
                "be_sl_level": Decimal("0.003"),
                "trail_pct": Decimal("0.005"),
            }
        ),
        "update_position_state_sl": AsyncMock(return_value=None),
        # T-533b2 sites #5/#6: patched no-op so MagicMock conn is not hit;
        # asserted by the lifecycle-state tests below.
        "update_trade_lifecycle_state": AsyncMock(return_value=None),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(lifecycle_mod, name, mock)
    return mocks


@pytest.fixture
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace asyncio.sleep inside lifecycle_mod with a no-op so ticks fire instantly."""

    async def _instant_sleep(_seconds: float) -> None:
        return None

    sleep_mock = AsyncMock(side_effect=_instant_sleep)
    monkeypatch.setattr("services.execution.app.lifecycle.asyncio.sleep", sleep_mock)
    return sleep_mock


# ---------------------------------------------------------------------------
# Helper isolation tests (no async loop)
# ---------------------------------------------------------------------------


def test_update_best_price_long_seeds_with_current_when_none() -> None:
    assert _update_best_price("buy", Decimal("100"), ps_best=None) == Decimal("100")


def test_update_best_price_short_only_on_lower() -> None:
    # Short: best is the LOWEST price observed.
    assert _update_best_price("sell", Decimal("95"), ps_best=Decimal("100")) == Decimal("95")
    assert _update_best_price("sell", Decimal("105"), ps_best=Decimal("100")) == Decimal("100")


def test_update_best_price_long_only_on_higher() -> None:
    assert _update_best_price("buy", Decimal("105"), ps_best=Decimal("100")) == Decimal("105")
    assert _update_best_price("buy", Decimal("95"), ps_best=Decimal("100")) == Decimal("100")


def test_update_mfe_mae_initial_seeds_both_with_current_price() -> None:
    mfe, mae = _update_mfe_mae("buy", Decimal("100"), ps_mfe=None, ps_mae=None)
    assert mfe == Decimal("100")
    assert mae == Decimal("100")


def test_update_mfe_mae_long_tracks_high_mfe_low_mae() -> None:
    # Long: MFE = highest, MAE = lowest.
    mfe, mae = _update_mfe_mae("buy", Decimal("110"), ps_mfe=Decimal("105"), ps_mae=Decimal("98"))
    assert mfe == Decimal("110")
    assert mae == Decimal("98")
    mfe, mae = _update_mfe_mae("buy", Decimal("95"), ps_mfe=Decimal("110"), ps_mae=Decimal("98"))
    assert mfe == Decimal("110")
    assert mae == Decimal("95")


def test_update_mfe_mae_short_tracks_low_mfe_high_mae() -> None:
    # Short: MFE = lowest (favorable for short), MAE = highest.
    mfe, mae = _update_mfe_mae("sell", Decimal("90"), ps_mfe=Decimal("95"), ps_mae=Decimal("100"))
    assert mfe == Decimal("90")
    assert mae == Decimal("100")
    mfe, mae = _update_mfe_mae("sell", Decimal("105"), ps_mfe=Decimal("90"), ps_mae=Decimal("100"))
    assert mfe == Decimal("90")
    assert mae == Decimal("105")


# ---------------------------------------------------------------------------
# run_position_monitor_for_trade — body tests
# ---------------------------------------------------------------------------


def _build_args(
    *,
    pool: MagicMock,
    bus: MagicMock,
    side: str = "buy",
    entry_price: Decimal = Decimal("100"),
    qty: Decimal = Decimal("10"),
    poll_interval_s: float = 0.001,
    stale_ticks_threshold: int = 5,
    adapter: MagicMock | None = None,
) -> dict[str, Any]:
    used_adapter = adapter if adapter is not None else MagicMock()
    if adapter is None:
        used_adapter.set_trading_stop = AsyncMock()
    # T-535 (L-015 generalized to this unit-test helper): the monitor now
    # calls adapter.get_positions(symbol) every tick (SL-overwrite check).
    # Default to a benign empty snapshot so existing body tests are
    # unaffected (no matching position → _detect_sl_overwrite returns,
    # no emit); tests exercising the check pre-set their own get_positions.
    if not isinstance(getattr(used_adapter, "get_positions", None), AsyncMock):
        used_adapter.get_positions = AsyncMock(return_value=[])
    return {
        "bot_id": BotId("alpha"),
        "symbol": "BTCUSDT",
        "trade_id": 1,
        "side": side,
        "entry_price": entry_price,
        "qty": qty,
        "pool": pool,
        "adapter": used_adapter,
        "bus": bus,
        "bound_logger": MagicMock(),
        "poll_interval_s": poll_interval_s,
        "stale_ticks_threshold": stale_ticks_threshold,
        "now_fn": lambda: _FIXED_NOW,
    }


async def test_run_position_monitor_exits_when_position_state_returns_none(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """Graceful self-cancel on T-219 close (position_state DELETEd)."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"100.5", 1))
    patched_queries["select_position_state"].return_value = None
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus)
    args["bound_logger"] = MagicMock()
    await run_position_monitor_for_trade(**args)
    info_event_names = [c.args[0] for c in args["bound_logger"].info.call_args_list]
    assert "execution.lifecycle_exit_position_closed" in info_event_names
    patched_queries["update_position_state_monitor_tick"].assert_not_called()


async def test_run_position_monitor_propagates_cancellederror_without_log_noise(
    patched_queries: dict[str, Any],
) -> None:
    """asyncio.CancelledError propagates cleanly without log noise."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"100", 1))
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, poll_interval_s=3600)
    task = asyncio.create_task(run_position_monitor_for_trade(**args))
    await asyncio.sleep(0)  # let task start the first sleep
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    error_event_names = [c.args[0] for c in args["bound_logger"].error.call_args_list]
    assert error_event_names == []


async def test_run_position_monitor_decodes_decimal_string_from_kv_bytes(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """KV bytes round-trip into Decimal-exact (no float coercion)."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"45100.25", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(entry_price=Decimal("45000.50"), qty=Decimal("0.01")),
        None,  # 2nd tick: exit
    ]
    pool = _build_pool()
    args = _build_args(
        pool=pool,
        bus=bus,
        side="buy",
        entry_price=Decimal("45000.50"),
        qty=Decimal("0.01"),
    )
    await run_position_monitor_for_trade(**args)
    update_call = patched_queries["update_position_state_monitor_tick"].call_args
    # running_pnl = (45100.25 - 45000.50) * 0.01 * 1 = 0.9975 (Fixture 3 hand-verified)
    assert update_call.kwargs["running_pnl"] == Decimal("0.9975")


async def test_run_position_monitor_logs_stale_pause_at_threshold_consecutive_misses(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """Stale-tick threshold WARN fires exactly once at consecutive missing reads."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(side_effect=[None, None, None, None, None])  # 5 misses
    patched_queries["select_position_state"].return_value = None  # never reached
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, stale_ticks_threshold=3)

    async def _stop_after_5(*_a: Any, **_k: Any) -> None:
        # Cancel after 5 KV miss attempts (>= threshold) so the loop exits.
        if bus.kv_get.await_count >= 5:
            raise asyncio.CancelledError

    bus.kv_get.side_effect = [None, None, None, None, asyncio.CancelledError()]
    args["bound_logger"] = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await run_position_monitor_for_trade(**args)
    warning_event_names = [c.args[0] for c in args["bound_logger"].warning.call_args_list]
    assert warning_event_names.count("execution.lifecycle_price_stale_pause") == 1


async def test_run_position_monitor_resets_stale_count_after_kv_recovery(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """Stale_count resets to 0 on first successful read; second cycle re-needs threshold."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(
        side_effect=[
            None,
            None,
            None,  # 3 misses → WARN (threshold=3)
            (b"100", 1),  # recovery → stale_count = 0
            None,  # 1 miss
            asyncio.CancelledError(),  # exit
        ]
    )
    patched_queries["select_position_state"].return_value = _ps_row()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, stale_ticks_threshold=3)
    args["bound_logger"] = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await run_position_monitor_for_trade(**args)
    warning_count = sum(
        1
        for c in args["bound_logger"].warning.call_args_list
        if c.args[0] == "execution.lifecycle_price_stale_pause"
    )
    assert warning_count == 1  # second cycle didn't re-hit threshold


async def test_run_position_monitor_logs_decode_error_on_malformed_kv_bytes(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    bus = MagicMock()
    bus.kv_get = AsyncMock(
        side_effect=[
            (b"not-a-number", 1),
            asyncio.CancelledError(),
        ]
    )
    patched_queries["select_position_state"].return_value = _ps_row()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus)
    args["bound_logger"] = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await run_position_monitor_for_trade(**args)
    warning_event_names = [c.args[0] for c in args["bound_logger"].warning.call_args_list]
    assert "execution.lifecycle_price_decode_error" in warning_event_names


async def test_run_position_monitor_updates_running_pnl_long_side_fixture_1(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """Fixture 1 — long, entry=100, qty=10, current=110 → running_pnl=100."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"110", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(side="buy", entry_price=Decimal("100"), qty=Decimal("10")),
        None,  # 2nd tick: exit
    ]
    pool = _build_pool()
    args = _build_args(
        pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), qty=Decimal("10")
    )
    await run_position_monitor_for_trade(**args)
    update_call = patched_queries["update_position_state_monitor_tick"].call_args
    assert update_call.kwargs["running_pnl"] == Decimal("100")
    assert update_call.kwargs["best_price"] == Decimal("110")  # long: high best


async def test_run_position_monitor_updates_running_pnl_short_side_fixture_2(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """Fixture 2 — short, entry=100 qty=5 current=90 → running_pnl=50; best_price=90."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"90", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(side="sell", entry_price=Decimal("100"), qty=Decimal("5")),
        None,
    ]
    pool = _build_pool()
    args = _build_args(
        pool=pool, bus=bus, side="sell", entry_price=Decimal("100"), qty=Decimal("5")
    )
    await run_position_monitor_for_trade(**args)
    update_call = patched_queries["update_position_state_monitor_tick"].call_args
    assert update_call.kwargs["running_pnl"] == Decimal("50")
    assert update_call.kwargs["best_price"] == Decimal("90")  # short: low best


async def test_run_position_monitor_uses_now_fn_for_updated_at(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """§N1 UTC pin — updated_at value comes from injected now_fn()."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"100", 1))
    patched_queries["select_position_state"].side_effect = [_ps_row(), None]
    pool = _build_pool()
    fixed_t = datetime(2026, 6, 1, 9, 30, 0, tzinfo=UTC)
    args = _build_args(pool=pool, bus=bus)
    args["now_fn"] = lambda: fixed_t
    await run_position_monitor_for_trade(**args)
    update_call = patched_queries["update_position_state_monitor_tick"].call_args
    assert update_call.kwargs["updated_at"] == fixed_t


# ---------------------------------------------------------------------------
# T-217b — BE trigger + trail SL adjustment helpers
# ---------------------------------------------------------------------------


from services.execution.app.lifecycle import (  # noqa: E402
    _check_be_trigger,
    _compute_be_sl_price,
    _compute_trail_sl_price,
)


def test_check_be_trigger_long_returns_true_at_or_above_threshold() -> None:
    """Fixture A — boundary: (100.5-100)/100 = 0.005 >= 0.005."""
    assert _check_be_trigger("buy", Decimal("100.5"), Decimal("100"), Decimal("0.005")) is True


def test_check_be_trigger_long_returns_false_below_threshold() -> None:
    """Fixture B — (100.4-100)/100 = 0.004 < 0.005."""
    assert _check_be_trigger("buy", Decimal("100.4"), Decimal("100"), Decimal("0.005")) is False


def test_check_be_trigger_short_returns_true_at_threshold() -> None:
    """Fixture C — (100-99.5)/100 = 0.005 >= 0.005."""
    assert _check_be_trigger("sell", Decimal("99.5"), Decimal("100"), Decimal("0.005")) is True


def test_compute_be_sl_price_long_adds_be_sl_level_to_entry() -> None:
    """100 * (1 + 0.003) = 100.300."""
    result = _compute_be_sl_price("buy", Decimal("100"), Decimal("0.003"))
    assert result == Decimal("100.300")


def test_compute_be_sl_price_short_subtracts_be_sl_level_from_entry() -> None:
    """100 * (1 - 0.003) = 99.700."""
    result = _compute_be_sl_price("sell", Decimal("100"), Decimal("0.003"))
    assert result == Decimal("99.700")


def test_compute_trail_sl_price_long_subtracts_trail_pct_from_best() -> None:
    """Fixture D — 110 * (1 - 0.005) = 109.450."""
    result = _compute_trail_sl_price("buy", Decimal("110"), Decimal("0.005"))
    assert result == Decimal("109.450")


def test_compute_trail_sl_price_short_adds_trail_pct_to_best() -> None:
    """Fixture E — 90 * (1 + 0.005) = 90.450."""
    result = _compute_trail_sl_price("sell", Decimal("90"), Decimal("0.005"))
    assert result == Decimal("90.450")


# ---------------------------------------------------------------------------
# T-217b — run_position_monitor_for_trade BE/trail body tests
# ---------------------------------------------------------------------------


async def test_run_position_monitor_be_trigger_invokes_set_trading_stop_with_explicit_full_mode(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """H-013 binding pin — BE call site uses tpsl_mode='Full' literal."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"100.5", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(side="buy", entry_price=Decimal("100"), sl_type="protective"),
        None,  # 2nd tick: exit
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    await run_position_monitor_for_trade(**args)
    adapter.set_trading_stop.assert_awaited_once()
    call_kwargs = adapter.set_trading_stop.call_args.kwargs
    assert call_kwargs["tpsl_mode"] == "Full"
    assert call_kwargs["sl_price"] == Decimal("100.300")  # 100 * 1.003


async def test_run_position_monitor_be_trigger_writes_sl_type_be_post_set_trading_stop_success(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """On set_trading_stop success → update_position_state_sl with sl_type='be'."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"100.5", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(side="buy", entry_price=Decimal("100"), sl_type="protective"),
        None,
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    await run_position_monitor_for_trade(**args)
    sl_call = patched_queries["update_position_state_sl"].call_args
    assert sl_call.kwargs["sl_type"] == "be"
    assert sl_call.kwargs["sl_price"] == Decimal("100.300")


async def test_run_position_monitor_be_trigger_writes_lifecycle_state_breakeven_set(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """T-533b2 site #5: BE-trigger SL move → update_trade_lifecycle_state(BREAKEVEN_SET)."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"100.5", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(side="buy", entry_price=Decimal("100"), sl_type="protective"),
        None,
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    await run_position_monitor_for_trade(**args)
    lc_call = patched_queries["update_trade_lifecycle_state"].call_args
    assert lc_call.kwargs["state"] == TradeLifecycleState.BREAKEVEN_SET
    assert lc_call.kwargs["trade_id"] == args["trade_id"]


async def test_run_position_monitor_be_trigger_idempotent_after_first_fire(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """sl_type='be' on second tick → BE branch does NOT re-fire."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"105", 1))
    # Tick 1: sl_type='be' (already past BE) → no fire.
    # Tick 2: None → exit.
    patched_queries["select_position_state"].side_effect = [
        _ps_row(side="buy", entry_price=Decimal("100"), sl_type="be"),
        None,
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    await run_position_monitor_for_trade(**args)
    adapter.set_trading_stop.assert_not_called()
    patched_queries["update_position_state_sl"].assert_not_called()


async def test_run_position_monitor_trail_update_skipped_when_best_price_unchanged(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """Fixture F — best_price unchanged → no set_trading_stop call (anti-spam)."""
    bus = MagicMock()
    # current=110 == ps.best_price=110 → new_best == ps.best_price → no movement.
    bus.kv_get = AsyncMock(return_value=(b"110", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(
            side="buy",
            entry_price=Decimal("100"),
            sl_type="trail",
            best_price=Decimal("110"),
        ),
        None,
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    await run_position_monitor_for_trade(**args)
    adapter.set_trading_stop.assert_not_called()
    patched_queries["update_position_state_sl"].assert_not_called()


async def test_run_position_monitor_trail_update_invokes_set_trading_stop_on_best_move(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """Long: best moves up 110 → 115 → set_trading_stop with 115*0.995=114.425."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"115", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(
            side="buy",
            entry_price=Decimal("100"),
            sl_type="trail",
            best_price=Decimal("110"),
        ),
        None,
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    await run_position_monitor_for_trade(**args)
    adapter.set_trading_stop.assert_awaited_once()
    call_kwargs = adapter.set_trading_stop.call_args.kwargs
    assert call_kwargs["sl_price"] == Decimal("114.425")  # 115 * 0.995


async def test_run_position_monitor_trail_update_writes_lifecycle_state_trailing_active(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """T-533b2 site #6: trail SL move → update_trade_lifecycle_state(TRAILING_ACTIVE)."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"115", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(
            side="buy",
            entry_price=Decimal("100"),
            sl_type="trail",
            best_price=Decimal("110"),
        ),
        None,
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    await run_position_monitor_for_trade(**args)
    lc_call = patched_queries["update_trade_lifecycle_state"].call_args
    assert lc_call.kwargs["state"] == TradeLifecycleState.TRAILING_ACTIVE
    assert lc_call.kwargs["trade_id"] == args["trade_id"]


async def test_run_position_monitor_trail_update_invokes_set_trading_stop_with_explicit_full_mode(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """H-013 binding pin — trail call site uses tpsl_mode='Full' literal (WG#14)."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"115", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(
            side="buy",
            entry_price=Decimal("100"),
            sl_type="trail",
            best_price=Decimal("110"),
        ),
        None,
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    await run_position_monitor_for_trade(**args)
    call_kwargs = adapter.set_trading_stop.call_args.kwargs
    assert call_kwargs["tpsl_mode"] == "Full"


async def test_run_position_monitor_be_set_failure_logs_error_and_continues_loop(
    patched_queries: dict[str, Any],
    fast_sleep: AsyncMock,
) -> None:
    """WG#13 / OQ-D — set_trading_stop exception → log ERROR + continue, no UPDATE."""
    from packages.exchange.errors import NetworkTimeout

    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"100.5", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(side="buy", entry_price=Decimal("100"), sl_type="protective"),
        None,
    ]
    adapter = MagicMock()
    adapter.set_trading_stop = AsyncMock(side_effect=NetworkTimeout("timeout"))
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, side="buy", entry_price=Decimal("100"), adapter=adapter)
    args["bound_logger"] = MagicMock()
    await run_position_monitor_for_trade(**args)
    error_event_names = [c.args[0] for c in args["bound_logger"].error.call_args_list]
    assert "execution.lifecycle_be_set_failed" in error_event_names
    # WG#16 else clause — no UPDATE on exception path.
    patched_queries["update_position_state_sl"].assert_not_called()


# ---------------------------------------------------------------------------
# T-535 / H-029 — _detect_sl_overwrite (out-of-FSM SL modification)
# ---------------------------------------------------------------------------


def _position(
    *,
    symbol: str = "BTCUSDT",
    size: Decimal = Decimal("10"),
    sl_price: Decimal | None = Decimal("90"),
) -> Position:
    return Position(
        symbol=symbol,
        side="buy",
        size=size,
        entry_price=Decimal("100"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        sl_price=sl_price,
    )


@pytest.fixture
def patched_insert_event(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(lifecycle_mod, "insert_trading_event", mock)
    return mock


def _detect_args(
    *,
    adapter: MagicMock,
    ps_sl_price: Decimal | None = Decimal("95"),
    ps_sl_type: str | None = "protective",
) -> dict[str, Any]:
    return {
        "conn": _FakeConn(),
        "adapter": adapter,
        "bound_logger": MagicMock(),
        "bot_id": BotId("alpha"),
        "symbol": "BTCUSDT",
        "trade_id": 7,
        "ps_sl_price": ps_sl_price,
        "ps_sl_type": ps_sl_type,
        "now_fn": lambda: _FIXED_NOW,
    }


async def test_sl_overwrite_detected_emits_trading_event(
    patched_insert_event: AsyncMock,
) -> None:
    """exchange_sl != ps.sl_price → one sl_overwrite_detected row + WARN."""
    adapter = MagicMock()
    adapter.get_positions = AsyncMock(return_value=[_position(sl_price=Decimal("90"))])
    args = _detect_args(adapter=adapter, ps_sl_price=Decimal("95"))
    await _detect_sl_overwrite(**args)

    patched_insert_event.assert_awaited_once()
    assert patched_insert_event.await_args is not None
    kw = patched_insert_event.await_args.kwargs
    assert kw["event_type"] == "sl_overwrite_detected"
    assert kw["bot_id"] == "alpha"
    assert kw["correlation_id"] == "sl-overwrite-alpha-BTCUSDT-7"
    assert kw["occurred_at"] == _FIXED_NOW
    payload = kw["payload"]
    # WG#2 / L-011/L-013 — payload is provably JSON-native.
    assert isinstance(payload["expected_sl_price"], str)
    assert isinstance(payload["observed_sl_price"], str)
    assert isinstance(payload["trade_id"], int)
    assert isinstance(payload["sl_type"], (str, type(None)))
    assert isinstance(payload["bot_id"], str)
    assert payload["expected_sl_price"] == "95"
    assert payload["observed_sl_price"] == "90"
    assert payload["trade_id"] == 7
    warn_names = [c.args[0] for c in args["bound_logger"].warning.call_args_list]
    assert "execution.sl_overwrite_detected" in warn_names


@pytest.mark.parametrize(
    "exchange_sl",
    [Decimal("95"), Decimal("95.00")],
)
async def test_sl_overwrite_no_false_positive_on_matching_sl(
    patched_insert_event: AsyncMock,
    exchange_sl: Decimal,
) -> None:
    """exchange_sl == ps.sl_price (incl. post-legit-trail steady state, and
    Decimal-equal-different-scale) → NO emit. H-029 false-positive pin.
    """
    adapter = MagicMock()
    adapter.get_positions = AsyncMock(return_value=[_position(sl_price=exchange_sl)])
    # Realistic post-legit-trail steady state: the prior tick's FSM trail
    # wrote BOTH exchange and DB to this value (L-017 — realistic pre-state,
    # not an artificial placeholder).
    await _detect_sl_overwrite(
        **_detect_args(adapter=adapter, ps_sl_price=Decimal("95"), ps_sl_type="trail"),
    )
    patched_insert_event.assert_not_awaited()


@pytest.mark.parametrize(
    "exc",
    [
        AuthError("a"),
        OrderRejected("o"),
        NetworkTimeout("n"),
        RateLimitError("r"),
        UnknownState("u"),
    ],
)
async def test_sl_overwrite_skipped_on_get_positions_error(
    patched_insert_event: AsyncMock,
    exc: Exception,
) -> None:
    """Transient/failed get_positions = uncertainty, NOT an overwrite."""
    adapter = MagicMock()
    adapter.get_positions = AsyncMock(side_effect=exc)
    args = _detect_args(adapter=adapter)
    await _detect_sl_overwrite(**args)
    patched_insert_event.assert_not_awaited()
    err_names = [c.args[0] for c in args["bound_logger"].error.call_args_list]
    assert "execution.sl_overwrite_check_failed" in err_names


async def test_sl_overwrite_skipped_when_exchange_sl_none(
    patched_insert_event: AsyncMock,
) -> None:
    """exchange_sl is None = SL removed (T-534b2/H-028 domain) / paper → defer."""
    adapter = MagicMock()
    adapter.get_positions = AsyncMock(return_value=[_position(sl_price=None)])
    await _detect_sl_overwrite(**_detect_args(adapter=adapter))
    patched_insert_event.assert_not_awaited()


async def test_sl_overwrite_skipped_when_position_flat_or_absent(
    patched_insert_event: AsyncMock,
) -> None:
    """No matching position, or size==0 (flat) → T-221/watchdog domain, no emit."""
    adapter = MagicMock()
    adapter.get_positions = AsyncMock(return_value=[])
    await _detect_sl_overwrite(**_detect_args(adapter=adapter))
    patched_insert_event.assert_not_awaited()

    adapter.get_positions = AsyncMock(
        return_value=[_position(size=Decimal("0"), sl_price=Decimal("90"))],
    )
    await _detect_sl_overwrite(**_detect_args(adapter=adapter))
    patched_insert_event.assert_not_awaited()


async def test_sl_overwrite_skipped_when_ps_sl_price_none(
    patched_insert_event: AsyncMock,
) -> None:
    """FSM has no SL on record → early return, get_positions not even awaited."""
    adapter = MagicMock()
    adapter.get_positions = AsyncMock(return_value=[_position()])
    await _detect_sl_overwrite(**_detect_args(adapter=adapter, ps_sl_price=None))
    adapter.get_positions.assert_not_awaited()
    patched_insert_event.assert_not_awaited()


async def test_run_position_monitor_invokes_sl_overwrite_then_continues(
    patched_queries: dict[str, Any],
    patched_insert_event: AsyncMock,
    fast_sleep: AsyncMock,
) -> None:
    """Call-site (WG#5): _detect_sl_overwrite runs between the monitor-tick
    update and the BE/trail block; a detected overwrite is emit-only and
    does NOT short-circuit the FSM (select_trade_fsm_params still reached).
    """
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"100", 1))
    patched_queries["select_position_state"].side_effect = [
        _ps_row(sl_price=Decimal("95"), sl_type="protective"),
        None,  # 2nd tick: graceful exit
    ]
    adapter = MagicMock()
    adapter.get_positions = AsyncMock(return_value=[_position(sl_price=Decimal("90"))])
    adapter.set_trading_stop = AsyncMock()
    pool = _build_pool()
    args = _build_args(pool=pool, bus=bus, adapter=adapter)
    await run_position_monitor_for_trade(**args)

    adapter.get_positions.assert_awaited_with("BTCUSDT")
    patched_insert_event.assert_awaited_once()  # 90 != 95 → detected
    # FSM continued past the check (OQ-2=A non-destructive): the BE/trail
    # path was reached on the same tick.
    patched_queries["select_trade_fsm_params"].assert_called()
