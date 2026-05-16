"""§N4 unit tests for :mod:`services.execution.app.sl_watchdog` (T-534b2 / H-028).

Mock-based (mirror ``test_equity_snapshot.py`` / ``test_audit.py``):
``pool.acquire`` ctx, ``adapter.get_positions`` per bot,
``select_position_states_for_bots`` + ``emergency_close_tracked_position``
patched on the module. Covers the H-028 false-positive guard (transient =
no-observation = streak preserved), the OQ-2=A ∧ OQ-3=A counter state
machine + prune semantics, paper-skip (OQ-4=A), orphan-exchange defer
(OQ-B=A), and ``now_fn`` injection. Zero arithmetic (Gate-4).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.execution import PositionStateRow
from packages.exchange.errors import AuthError, NetworkTimeout, RateLimitError
from packages.exchange.types import Position
from services.execution.app import sl_watchdog as sw_mod
from services.execution.app.sl_watchdog import run_sl_watchdog_tick

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from packages.core import BotId
    from packages.exchange.protocols import ExchangeClient

_FIXED_NOW = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


class _FakeConn:
    pass


def _build_pool() -> MagicMock:
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[_FakeConn]:
        yield _FakeConn()

    pool.acquire = _acquire
    return pool


def _pos(symbol: str, *, size: str = "0.05", sl_price: str | None = None) -> Position:
    """Open position (size>0) with `sl_price` absent unless given."""
    return Position(
        symbol=symbol,
        side="buy",
        size=Decimal(size),
        entry_price=Decimal("45000"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        sl_price=None if sl_price is None else Decimal(sl_price),
    )


def _ps(bot_id: str, symbol: str, *, trade_id: int = 42) -> PositionStateRow:
    return PositionStateRow(
        bot_id=bot_id,
        symbol=symbol,
        trade_id=trade_id,
        side="buy",
        entry_price=Decimal("45000"),
        qty=Decimal("0.05"),
        remaining_qty=Decimal("0.05"),
        sl_price=None,
        tp_price=None,
        sl_type=None,
    )


def _adapter(positions: list[Position] | Exception) -> MagicMock:
    adapter = MagicMock()
    if isinstance(positions, Exception):
        adapter.get_positions = AsyncMock(side_effect=positions)
    else:
        adapter.get_positions = AsyncMock(return_value=positions)
    return adapter


def _adapters(**pairs: MagicMock) -> dict[BotId, ExchangeClient]:
    return cast("dict[BotId, ExchangeClient]", dict(pairs))


def _paper(*bot_ids: str) -> frozenset[BotId]:
    return cast("frozenset[BotId]", frozenset(bot_ids))


@pytest.fixture
def patched_ps(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch select_position_states_for_bots on the module (returns [] by default)."""
    mock = AsyncMock(return_value=[])
    monkeypatch.setattr(sw_mod, "select_position_states_for_bots", mock)
    return mock


@pytest.fixture
def patched_close(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch emergency_close_tracked_position on the module (graceful no-op)."""
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(sw_mod, "emergency_close_tracked_position", mock)
    return mock


async def _tick(
    *,
    adapters: dict[BotId, ExchangeClient],
    counters: dict[tuple[str, str], int],
    paper: frozenset[BotId] | None = None,
    threshold: int = 3,
) -> None:
    await run_sl_watchdog_tick(
        pool=_build_pool(),
        adapters=adapters,
        paper_bot_ids=paper if paper is not None else _paper(),
        bus=MagicMock(),
        sl_miss_counters=counters,
        missing_threshold_ticks=threshold,
        bound_logger=MagicMock(),
        now_fn=lambda: _FIXED_NOW,
    )


@pytest.mark.asyncio
async def test_sl_watchdog_emergency_closes_after_n_consecutive_missing(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """H-028 pin: N-1 missing ticks no close → Nth fires once + counter cleared."""
    patched_ps.return_value = [_ps("alpha", "BTCUSDT")]
    adapters = _adapters(alpha=_adapter([_pos("BTCUSDT")]))
    counters: dict[tuple[str, str], int] = {}

    await _tick(adapters=adapters, counters=counters)
    assert counters[("alpha", "BTCUSDT")] == 1
    await _tick(adapters=adapters, counters=counters)
    assert counters[("alpha", "BTCUSDT")] == 2
    patched_close.assert_not_called()

    await _tick(adapters=adapters, counters=counters)
    patched_close.assert_awaited_once()
    kwargs = patched_close.await_args
    assert kwargs is not None
    assert kwargs.kwargs["bot_id"] == "alpha"
    assert kwargs.kwargs["ps_row"].trade_id == 42
    assert kwargs.kwargs["now_fn"]() == _FIXED_NOW
    # OQ-2=A — counter cleared post-fire.
    assert ("alpha", "BTCUSDT") not in counters


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [NetworkTimeout("t"), RateLimitError("r"), AuthError("a")],
)
async def test_sl_watchdog_transient_error_does_not_increment_counter(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
    exc: Exception,
) -> None:
    """H-028 pin: transient get_positions = no-observation; streak preserved."""
    patched_ps.return_value = [_ps("alpha", "BTCUSDT")]
    counters: dict[tuple[str, str], int] = {}

    # Tick 1: confirmed missing → count 1.
    await _tick(
        adapters=_adapters(alpha=_adapter([_pos("BTCUSDT")])),
        counters=counters,
    )
    assert counters[("alpha", "BTCUSDT")] == 1

    # Tick 2: transient → count UNCHANGED (not pruned, not incremented).
    await _tick(adapters=_adapters(alpha=_adapter(exc)), counters=counters)
    assert counters[("alpha", "BTCUSDT")] == 1
    patched_close.assert_not_called()

    # Tick 3: confirmed missing again → streak resumes at 2 (blip survived).
    await _tick(
        adapters=_adapters(alpha=_adapter([_pos("BTCUSDT")])),
        counters=counters,
    )
    assert counters[("alpha", "BTCUSDT")] == 2
    patched_close.assert_not_called()


@pytest.mark.asyncio
async def test_sl_watchdog_broad_error_skips_bot_no_raise(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """OQ-3=A: non-transient exception → error-log, continue, no raise, untouched."""
    patched_ps.return_value = [_ps("alpha", "BTCUSDT"), _ps("beta", "ETHUSDT")]
    counters: dict[tuple[str, str], int] = {("alpha", "BTCUSDT"): 2}
    adapters = _adapters(
        alpha=_adapter(ValueError("boom")),
        beta=_adapter([_pos("ETHUSDT")]),
    )

    await _tick(adapters=adapters, counters=counters)

    # alpha errored → counter preserved untouched (not pruned).
    assert counters[("alpha", "BTCUSDT")] == 2
    # beta observed + missing → its own counter advanced.
    assert counters[("beta", "ETHUSDT")] == 1
    patched_close.assert_not_called()


@pytest.mark.asyncio
async def test_sl_watchdog_sl_restored_resets_counter(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """OQ-2=A: observed SL-present pops the streak."""
    patched_ps.return_value = [_ps("alpha", "BTCUSDT")]
    counters: dict[tuple[str, str], int] = {("alpha", "BTCUSDT"): 2}

    await _tick(
        adapters=_adapters(alpha=_adapter([_pos("BTCUSDT", sl_price="44000")])),
        counters=counters,
    )
    assert ("alpha", "BTCUSDT") not in counters
    patched_close.assert_not_called()


@pytest.mark.asyncio
async def test_sl_watchdog_skips_paper_bots(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """OQ-4=A: paper bots skipped — get_positions never awaited, no counter."""
    paper_adapter = _adapter([_pos("BTCUSDT")])
    counters: dict[tuple[str, str], int] = {}

    await _tick(
        adapters=_adapters(paperbot=paper_adapter),
        counters=counters,
        paper=_paper("paperbot"),
    )

    paper_adapter.get_positions.assert_not_awaited()
    assert counters == {}
    patched_ps.assert_not_awaited()  # no live bots → no DB roundtrip


@pytest.mark.asyncio
async def test_sl_watchdog_no_live_bots_no_op(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """All bots paper → no-op, no DB roundtrip, no counter mutation."""
    counters: dict[tuple[str, str], int] = {}
    await _tick(
        adapters=_adapters(p1=_adapter([]), p2=_adapter([])),
        counters=counters,
        paper=_paper("p1", "p2"),
    )
    patched_ps.assert_not_awaited()
    patched_close.assert_not_called()


@pytest.mark.asyncio
async def test_sl_watchdog_orphan_exchange_defers_no_action(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """OQ-B=A: exchange position with no DB ps_row → defer-log, no counter, no close."""
    patched_ps.return_value = []  # no DB rows for alpha
    counters: dict[tuple[str, str], int] = {}

    await _tick(
        adapters=_adapters(alpha=_adapter([_pos("BTCUSDT")])),
        counters=counters,
    )
    assert counters == {}
    patched_close.assert_not_called()


@pytest.mark.asyncio
async def test_sl_watchdog_flat_position_prunes_stale_counter(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """OQ-2=A: observed bot, position now flat (size==0 filtered) → counter pruned."""
    patched_ps.return_value = [_ps("alpha", "BTCUSDT")]
    counters: dict[tuple[str, str], int] = {("alpha", "BTCUSDT"): 2}

    # get_positions returns the symbol but size==0 → excluded by size>0 filter.
    await _tick(
        adapters=_adapters(alpha=_adapter([_pos("BTCUSDT", size="0")])),
        counters=counters,
    )
    assert ("alpha", "BTCUSDT") not in counters
    patched_close.assert_not_called()


@pytest.mark.asyncio
async def test_sl_watchdog_prune_skips_errored_bot_keys(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """OQ-2=A ∧ OQ-3=A: prune drops observed-ineligible only; errored-bot preserved."""
    patched_ps.return_value = [_ps("alpha", "BTCUSDT"), _ps("beta", "ETHUSDT")]
    counters: dict[tuple[str, str], int] = {
        ("alpha", "BTCUSDT"): 2,  # alpha will error → preserved
        ("beta", "ETHUSDT"): 2,  # beta observed, SL restored → pruned
    }
    adapters = _adapters(
        alpha=_adapter(NetworkTimeout("blip")),
        beta=_adapter([_pos("ETHUSDT", sl_price="3000")]),
    )

    await _tick(adapters=adapters, counters=counters)

    assert counters[("alpha", "BTCUSDT")] == 2  # errored → untouched
    assert ("beta", "ETHUSDT") not in counters  # observed-ineligible → pruned


@pytest.mark.asyncio
async def test_sl_watchdog_sub_threshold_does_not_close(
    patched_ps: AsyncMock,
    patched_close: AsyncMock,
) -> None:
    """Threshold semantics: count < N never fires; fires exactly on Nth."""
    patched_ps.return_value = [_ps("alpha", "BTCUSDT")]
    adapters = _adapters(alpha=_adapter([_pos("BTCUSDT")]))
    counters: dict[tuple[str, str], int] = {}

    await _tick(adapters=adapters, counters=counters, threshold=2)
    patched_close.assert_not_called()
    assert counters[("alpha", "BTCUSDT")] == 1

    await _tick(adapters=adapters, counters=counters, threshold=2)
    patched_close.assert_awaited_once()
