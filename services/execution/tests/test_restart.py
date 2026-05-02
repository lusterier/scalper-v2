"""§N4 unit tests for :mod:`services.execution.app.restart` (T-221).

Mock-based: pool.acquire ctx (with conn.transaction()), per-bot adapter
get_positions + place_market_order, patched query helpers
(select_position_states_for_bots, select_open_order_id_by_trade_id,
select_recent_open_trade_exists, update_trade_close, delete_position_state,
run_position_monitor_for_trade) on restart_mod. Validates H-020 5-step flow,
H-026 partial-non-50/50 verbatim hazard pin, 6 hand-fixtures A-F,
race-window guard semantics, fail-fast on get_positions, paper-mode parity.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.execution import PositionStateRow
from packages.exchange.types import Position
from services.execution.app import restart as restart_mod
from services.execution.app.restart import reconcile_on_startup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


class _FakeConn:
    def transaction(self) -> Any:
        @asynccontextmanager
        async def _tx() -> AsyncIterator[None]:
            yield

        return _tx()


def _build_pool() -> MagicMock:
    conn = _FakeConn()
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    pool.acquire = _acquire
    return pool


def _make_adapter(positions: list[Position]) -> MagicMock:
    adapter = MagicMock()
    adapter.get_positions = AsyncMock(return_value=positions)
    adapter.place_market_order = AsyncMock(return_value=None)
    return adapter


def _ps_row(
    *,
    bot_id: str = "alpha",
    symbol: str = "BTCUSDT",
    trade_id: int = 42,
    side: str = "buy",
    entry_price: Decimal = Decimal("50000"),
    qty: Decimal = Decimal("0.1"),
) -> PositionStateRow:
    return PositionStateRow(
        bot_id=bot_id,
        symbol=symbol,
        trade_id=trade_id,
        side=side,  # type: ignore[arg-type]
        entry_price=entry_price,
        qty=qty,
        remaining_qty=qty,
        sl_price=None,
        tp_price=None,
        sl_type="protective",
    )


def _exchange_pos(
    *,
    symbol: str = "BTCUSDT",
    side: str = "buy",
    size: Decimal = Decimal("0.1"),
) -> Position:
    return Position(
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        size=size,
        entry_price=Decimal("50000"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
    )


@pytest.fixture
def patched_queries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    spawned_tasks: list[MagicMock] = []

    def _create_task_mock(coro: Any, *, name: str = "") -> MagicMock:
        coro.close()
        m = MagicMock()
        m.cancel = MagicMock()
        m._name = name
        spawned_tasks.append(m)
        return m

    mocks: dict[str, Any] = {
        "select_position_states_for_bots": AsyncMock(return_value=[]),
        "select_open_order_id_by_trade_id": AsyncMock(return_value=99),
        "select_recent_open_trade_exists": AsyncMock(return_value=False),
        "update_trade_close": AsyncMock(return_value=None),
        "delete_position_state": AsyncMock(return_value=None),
        "run_position_monitor_for_trade": MagicMock(return_value=MagicMock()),
        "spawned_tasks": spawned_tasks,
    }
    for name in (
        "select_position_states_for_bots",
        "select_open_order_id_by_trade_id",
        "select_recent_open_trade_exists",
        "update_trade_close",
        "delete_position_state",
        "run_position_monitor_for_trade",
    ):
        monkeypatch.setattr(restart_mod, name, mocks[name])
    import asyncio as _asyncio_mod

    monkeypatch.setattr(_asyncio_mod, "create_task", _create_task_mock)
    return mocks


def _kwargs(
    *,
    adapters: dict[str, MagicMock] | None = None,
    race_window_seconds: int = 60,
    position_lifecycle_tasks: dict[int, Any] | None = None,
) -> dict[str, Any]:
    if adapters is None:
        adapters = {"alpha": _make_adapter([])}
    return {
        "pool": _build_pool(),
        "bus": MagicMock(),
        "adapters": adapters,
        "position_lifecycle_tasks": position_lifecycle_tasks
        if position_lifecycle_tasks is not None
        else {},
        "race_window_seconds": race_window_seconds,
        "position_poll_interval_s": 1.0,
        "position_poll_stale_ticks": 5,
        "bound_logger": MagicMock(),
        "now_fn": lambda: _FIXED_NOW,
    }


# ---------------------------------------------------------------------------
# Hazard test pins (verbatim brief §20 H-020 + H-026)
# ---------------------------------------------------------------------------


async def test_reconciliation_closes_db_orphans_and_markets_exchange_orphans(
    patched_queries: dict[str, Any],
) -> None:
    """H-020 verbatim: 1 matching + 1 orphan_db + 1 orphan_ex (outside race) → all 3 paths fire."""
    matching_row = _ps_row(symbol="BTCUSDT", trade_id=10)
    orphan_db_row = _ps_row(symbol="ETHUSDT", trade_id=20)
    patched_queries["select_position_states_for_bots"].return_value = [matching_row, orphan_db_row]

    adapter = _make_adapter(
        [
            _exchange_pos(symbol="BTCUSDT"),  # matches matching_row
            _exchange_pos(symbol="LTCUSDT"),  # orphan_ex
        ]
    )
    await reconcile_on_startup(**_kwargs(adapters={"alpha": adapter}))

    patched_queries["update_trade_close"].assert_called_once()
    update_kwargs = patched_queries["update_trade_close"].call_args.kwargs
    assert update_kwargs["trade_id"] == 20
    assert update_kwargs["close_reason"] == "reconcile_gone"
    patched_queries["delete_position_state"].assert_called_once()
    adapter.place_market_order.assert_called_once_with(
        symbol="LTCUSDT",
        side="sell",
        qty=Decimal("0.1"),
        reduce_only=True,
    )
    assert len(patched_queries["spawned_tasks"]) == 1


async def test_partial_non_5050_reconciliation_does_not_create_duplicate(
    patched_queries: dict[str, Any],
) -> None:
    """H-026 verbatim: position_state qty=10 vs exchange size=4.7 → MATCHING (PK only).

    Per H-026 the match is on (bot_id, symbol) PK, NEVER on qty. Even with
    a 50% qty divergence (post-partial-TP scenario before our ingest), the
    monitor task is rehydrated and NO market_close is issued.
    """
    matching_row = _ps_row(
        symbol="LTCUSDT",
        trade_id=88,
        qty=Decimal("10"),
    )
    patched_queries["select_position_states_for_bots"].return_value = [matching_row]
    adapter = _make_adapter(
        [_exchange_pos(symbol="LTCUSDT", size=Decimal("4.7"))],  # qty divergence
    )

    await reconcile_on_startup(**_kwargs(adapters={"alpha": adapter}))

    adapter.place_market_order.assert_not_called()
    patched_queries["update_trade_close"].assert_not_called()
    patched_queries["delete_position_state"].assert_not_called()
    assert len(patched_queries["spawned_tasks"]) == 1


# ---------------------------------------------------------------------------
# Hand-fixtures A-F (atomic paths)
# ---------------------------------------------------------------------------


async def test_matching_position_spawns_monitor_task(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture A — pos+ps for same (bot, symbol) → 1 monitor task spawned, 0 close, 0 market."""
    patched_queries["select_position_states_for_bots"].return_value = [
        _ps_row(symbol="BTCUSDT", trade_id=42)
    ]
    adapter = _make_adapter([_exchange_pos(symbol="BTCUSDT")])

    tasks: dict[int, Any] = {}
    await reconcile_on_startup(
        **_kwargs(adapters={"alpha": adapter}, position_lifecycle_tasks=tasks)
    )

    assert 42 in tasks
    patched_queries["run_position_monitor_for_trade"].assert_called_once()
    rcall = patched_queries["run_position_monitor_for_trade"].call_args.kwargs
    assert rcall["trade_id"] == 42
    assert rcall["symbol"] == "BTCUSDT"
    assert rcall["entry_price"] == Decimal("50000")
    adapter.place_market_order.assert_not_called()
    patched_queries["update_trade_close"].assert_not_called()


async def test_orphan_db_closes_with_reconcile_gone_reason(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture B — DB row exists, exchange returns []. open_order_id=99 → close_order_id=99."""
    patched_queries["select_position_states_for_bots"].return_value = [
        _ps_row(symbol="BTCUSDT", trade_id=42)
    ]
    patched_queries["select_open_order_id_by_trade_id"].return_value = 99
    adapter = _make_adapter([])

    await reconcile_on_startup(**_kwargs(adapters={"alpha": adapter}))

    update_kwargs = patched_queries["update_trade_close"].call_args.kwargs
    assert update_kwargs["trade_id"] == 42
    assert update_kwargs["close_order_id"] == 99
    assert update_kwargs["close_reason"] == "reconcile_gone"
    assert update_kwargs["exit_price"] == Decimal("0")
    assert update_kwargs["realized_pnl"] == Decimal("0")
    assert update_kwargs["fees_paid"] is None
    delete_kwargs = patched_queries["delete_position_state"].call_args.kwargs
    assert delete_kwargs == {"bot_id": "alpha", "symbol": "BTCUSDT"}


async def test_orphan_exchange_market_closes_outside_race_window(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture C — BTCUSDT buy 0.05, no DB, no race → market sell 0.05."""
    patched_queries["select_position_states_for_bots"].return_value = []
    patched_queries["select_recent_open_trade_exists"].return_value = False
    adapter = _make_adapter([_exchange_pos(symbol="BTCUSDT", side="buy", size=Decimal("0.05"))])

    await reconcile_on_startup(**_kwargs(adapters={"alpha": adapter}))

    adapter.place_market_order.assert_called_once_with(
        symbol="BTCUSDT",
        side="sell",
        qty=Decimal("0.05"),
        reduce_only=True,
    )


async def test_orphan_exchange_skips_inside_race_window(
    patched_queries: dict[str, Any],
) -> None:
    """Fixture D — exchange has BTCUSDT, recent open trade exists → 0 market_close (race window)."""
    patched_queries["select_position_states_for_bots"].return_value = []
    patched_queries["select_recent_open_trade_exists"].return_value = True
    adapter = _make_adapter([_exchange_pos(symbol="BTCUSDT")])

    await reconcile_on_startup(**_kwargs(adapters={"alpha": adapter}))

    adapter.place_market_order.assert_not_called()
    # Verify since= was now - 60s (race_window_seconds default).
    rcall = patched_queries["select_recent_open_trade_exists"].call_args.kwargs
    assert rcall["since"] == _FIXED_NOW - timedelta(seconds=60)
    assert rcall["bot_id"] == "alpha"
    assert rcall["symbol"] == "BTCUSDT"


async def test_orphan_exchange_short_side_closes_with_buy(
    patched_queries: dict[str, Any],
) -> None:
    """Side flip: exchange position side='sell' → market_close side='buy'."""
    patched_queries["select_position_states_for_bots"].return_value = []
    adapter = _make_adapter([_exchange_pos(symbol="BTCUSDT", side="sell", size=Decimal("0.05"))])

    await reconcile_on_startup(**_kwargs(adapters={"alpha": adapter}))

    adapter.place_market_order.assert_called_once_with(
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("0.05"),
        reduce_only=True,
    )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_empty_adapter_pool_no_op(patched_queries: dict[str, Any]) -> None:
    """No adapters configured → early-return, no DB read, no log noise."""
    await reconcile_on_startup(**_kwargs(adapters={}))
    patched_queries["select_position_states_for_bots"].assert_not_called()


async def test_get_positions_failure_raises(
    patched_queries: dict[str, Any],
) -> None:
    """OQ-5: adapter.get_positions raises → reraised from reconcile (fail-fast lifespan)."""
    adapter = _make_adapter([])
    adapter.get_positions = AsyncMock(side_effect=RuntimeError("api error"))

    with pytest.raises(RuntimeError, match="api error"):
        await reconcile_on_startup(**_kwargs(adapters={"alpha": adapter}))


async def test_place_market_order_failure_logs_and_continues(
    patched_queries: dict[str, Any],
) -> None:
    """OQ-5 amendment: place_market_order failure → ERROR log + continue (best-effort)."""
    patched_queries["select_position_states_for_bots"].return_value = []
    adapter = _make_adapter(
        [
            _exchange_pos(symbol="BTCUSDT", size=Decimal("0.05")),
            _exchange_pos(symbol="ETHUSDT", size=Decimal("0.5")),
        ]
    )
    adapter.place_market_order = AsyncMock(side_effect=[RuntimeError("timeout"), None])

    bound_logger = MagicMock()
    kwargs = _kwargs(adapters={"alpha": adapter})
    kwargs["bound_logger"] = bound_logger
    await reconcile_on_startup(**kwargs)

    assert adapter.place_market_order.call_count == 2
    error_calls = [
        call
        for call in bound_logger.error.call_args_list
        if call.args and call.args[0] == "reconcile.orphan_exchange_close_failed"
    ]
    assert len(error_calls) == 1


async def test_orphan_db_open_order_missing_logs_error_and_continues(
    patched_queries: dict[str, Any],
) -> None:
    """select_open_order_id_by_trade_id returns None → ERROR + continue, no update_trade_close."""
    patched_queries["select_position_states_for_bots"].return_value = [
        _ps_row(symbol="BTCUSDT", trade_id=42)
    ]
    patched_queries["select_open_order_id_by_trade_id"].return_value = None
    adapter = _make_adapter([])

    bound_logger = MagicMock()
    kwargs = _kwargs(adapters={"alpha": adapter})
    kwargs["bound_logger"] = bound_logger
    await reconcile_on_startup(**kwargs)

    patched_queries["update_trade_close"].assert_not_called()
    patched_queries["delete_position_state"].assert_not_called()
    error_calls = [
        call
        for call in bound_logger.error.call_args_list
        if call.args and call.args[0] == "reconcile.orphan_db_open_order_missing"
    ]
    assert len(error_calls) == 1


async def test_position_size_zero_filtered_out(
    patched_queries: dict[str, Any],
) -> None:
    """Position(size=0) is flat per type docstring → not orphan_ex, no market_close."""
    patched_queries["select_position_states_for_bots"].return_value = []
    flat_pos = Position(
        symbol="BTCUSDT",
        side=None,
        size=Decimal("0"),
        entry_price=None,
        leverage=None,
        unrealized_pnl=None,
    )
    adapter = _make_adapter([flat_pos])

    await reconcile_on_startup(**_kwargs(adapters={"alpha": adapter}))

    adapter.place_market_order.assert_not_called()


async def test_multi_bot_no_cross_bleed(patched_queries: dict[str, Any]) -> None:
    """2 bots, distinct DB rows + distinct adapters → each bot's positions handled independently."""
    patched_queries["select_position_states_for_bots"].return_value = [
        _ps_row(bot_id="alpha", symbol="BTCUSDT", trade_id=10),
        _ps_row(bot_id="beta", symbol="ETHUSDT", trade_id=20),
    ]
    adapter_alpha = _make_adapter([_exchange_pos(symbol="BTCUSDT")])
    adapter_beta = _make_adapter([_exchange_pos(symbol="ETHUSDT")])

    tasks: dict[int, Any] = {}
    await reconcile_on_startup(
        **_kwargs(
            adapters={"alpha": adapter_alpha, "beta": adapter_beta},
            position_lifecycle_tasks=tasks,
        )
    )

    assert 10 in tasks
    assert 20 in tasks
    adapter_alpha.place_market_order.assert_not_called()
    adapter_beta.place_market_order.assert_not_called()


async def test_orphan_db_close_emits_audit_log_keys(
    patched_queries: dict[str, Any],
) -> None:
    """orphan_db_closed log carries bot_id, trade_id, symbol, close_order_id."""
    patched_queries["select_position_states_for_bots"].return_value = [
        _ps_row(symbol="BTCUSDT", trade_id=42)
    ]
    patched_queries["select_open_order_id_by_trade_id"].return_value = 99
    adapter = _make_adapter([])

    bound_logger = MagicMock()
    kwargs = _kwargs(adapters={"alpha": adapter})
    kwargs["bound_logger"] = bound_logger
    await reconcile_on_startup(**kwargs)

    warning_calls = [
        call
        for call in bound_logger.warning.call_args_list
        if call.args and call.args[0] == "reconcile.orphan_db_closed"
    ]
    assert len(warning_calls) == 1
    log_kwargs = warning_calls[0].kwargs
    assert log_kwargs["bot_id"] == "alpha"
    assert log_kwargs["trade_id"] == 42
    assert log_kwargs["symbol"] == "BTCUSDT"
    assert log_kwargs["close_order_id"] == 99


async def test_paper_mode_parity_invokes_same_flow(
    patched_queries: dict[str, Any],
) -> None:
    """Paper adapter (Decision #8 sub_account==bot_id 1:1) goes through identical flow."""
    patched_queries["select_position_states_for_bots"].return_value = [
        _ps_row(bot_id="paper_bot", symbol="BTCUSDT", trade_id=7)
    ]
    paper_adapter = _make_adapter([_exchange_pos(symbol="BTCUSDT")])

    tasks: dict[int, Any] = {}
    await reconcile_on_startup(
        **_kwargs(
            adapters={"paper_bot": paper_adapter},
            position_lifecycle_tasks=tasks,
        )
    )

    assert 7 in tasks
    paper_adapter.get_positions.assert_called_once()


async def test_race_window_since_uses_now_fn_minus_seconds(
    patched_queries: dict[str, Any],
) -> None:
    """`since=now - race_window_seconds` boundary test (verifies timedelta math)."""
    patched_queries["select_position_states_for_bots"].return_value = []
    patched_queries["select_recent_open_trade_exists"].return_value = False
    adapter = _make_adapter([_exchange_pos(symbol="BTCUSDT")])

    custom_window = 120
    await reconcile_on_startup(
        **_kwargs(adapters={"alpha": adapter}, race_window_seconds=custom_window)
    )

    rcall = patched_queries["select_recent_open_trade_exists"].call_args.kwargs
    assert rcall["since"] == _FIXED_NOW - timedelta(seconds=120)
