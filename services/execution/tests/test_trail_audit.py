"""§N4 unit tests for :mod:`services.execution.app.trail_audit` (T-536).

Mock-based (mirror ``test_sl_watchdog.py``): ``pool.acquire`` ctx,
``adapter.get_positions`` per bot, ``select_position_states_for_bots`` +
``select_trade_fsm_params`` + ``insert_trading_event`` patched on the
module. ``_compute_trail_sl_price`` is **NOT** patched — reused real is
the cross-check value (OQ-2=A / L-003; the audit verifies the exchange
against the same FSM function). Covers the relative-drift math
(Gate-4 — REAL Decimal division), the disjoint-seam skips
(non-trail / best_price None / fsm None / pos None / exchange_sl None),
the transient false-positive guard, paper-skip, the strict-`>` tolerance
boundary, the div-by-zero guard, and ``now_fn`` injection.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.execution import PositionStateRow
from packages.exchange.errors import (
    AuthError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
    UnknownState,
)
from packages.exchange.types import InstrumentInfo, Position
from services.execution.app import trail_audit as ta_mod
from services.execution.app.trail_audit import run_trail_audit_tick

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


def _pos(
    symbol: str,
    *,
    size: str = "0.05",
    sl_price: str | None = "99.5",
    side: str = "buy",
) -> Position:
    return Position(
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        size=Decimal(size),
        entry_price=Decimal("100"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        sl_price=None if sl_price is None else Decimal(sl_price),
    )


def _ps_trail(
    bot_id: str,
    symbol: str,
    *,
    trade_id: int = 42,
    side: str = "buy",
    best_price: str | None = "100",
    sl_type: str | None = "trail",
) -> PositionStateRow:
    return PositionStateRow(
        bot_id=bot_id,
        symbol=symbol,
        trade_id=trade_id,
        side=side,  # type: ignore[arg-type]
        entry_price=Decimal("100"),
        qty=Decimal("0.05"),
        remaining_qty=Decimal("0.05"),
        sl_price=Decimal("99.5"),
        tp_price=None,
        sl_type=sl_type,
        best_price=None if best_price is None else Decimal(best_price),
    )


def _adapter(positions: list[Position] | Exception, *, tick: str = "0.001") -> MagicMock:
    adapter = MagicMock()
    if isinstance(positions, Exception):
        adapter.get_positions = AsyncMock(side_effect=positions)
    else:
        adapter.get_positions = AsyncMock(return_value=positions)
    # T-558b2 (L-034): trail_audit now calls adapter.get_instrument_info(ps.symbol)
    # for the tick (quantize the recomputed `expected`). Default tick 0.001 →
    # existing on-grid fixtures (expected 99.500, exchange 99.0/99.5) quantize
    # to themselves (no-op) → zero drift-behaviour ripple; the no-false-drift
    # test passes tick="0.1" to exercise the quantize.
    adapter.get_instrument_info = AsyncMock(
        return_value=InstrumentInfo(
            symbol="BTCUSDT",
            qty_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            min_notional_usd=Decimal("5"),
            tick_size=Decimal(tick),
        )
    )
    return adapter


def _adapters(**pairs: MagicMock) -> dict[BotId, ExchangeClient]:
    return cast("dict[BotId, ExchangeClient]", dict(pairs))


def _paper(*bot_ids: str) -> frozenset[BotId]:
    return cast("frozenset[BotId]", frozenset(bot_ids))


@pytest.fixture
def patched_ps(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch select_position_states_for_bots on the module ([] by default)."""
    mock = AsyncMock(return_value=[])
    monkeypatch.setattr(ta_mod, "select_position_states_for_bots", mock)
    return mock


@pytest.fixture
def patched_fsm(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch select_trade_fsm_params (trail_pct=0.005 by default)."""
    mock = AsyncMock(
        return_value={
            "be_trigger": Decimal("0.005"),
            "be_sl_level": Decimal("0.003"),
            "trail_pct": Decimal("0.005"),
        },
    )
    monkeypatch.setattr(ta_mod, "select_trade_fsm_params", mock)
    return mock


@pytest.fixture
def patched_insert(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch insert_trading_event on the module (no-op)."""
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(ta_mod, "insert_trading_event", mock)
    return mock


async def _tick(
    *,
    adapters: dict[BotId, ExchangeClient],
    paper: frozenset[BotId] | None = None,
    tolerance: str = "0.001",
) -> None:
    await run_trail_audit_tick(
        pool=_build_pool(),
        adapters=adapters,
        paper_bot_ids=paper if paper is not None else _paper(),
        drift_tolerance_pct=Decimal(tolerance),
        bound_logger=MagicMock(),
        now_fn=lambda: _FIXED_NOW,
    )


@pytest.mark.asyncio
async def test_trail_audit_emits_on_drift_beyond_tolerance(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """buy best=100 trail_pct=0.005 → expected 99.5; exchange 99.0 →
    drift 0.5/99.5≈0.005025 > 0.001 → emit trail_sl_drift_detected.
    """
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT")]
    adapters = _adapters(alpha=_adapter([_pos("BTCUSDT", sl_price="99.0")]))

    await _tick(adapters=adapters)

    patched_insert.assert_awaited_once()
    assert patched_insert.await_args is not None
    kw = patched_insert.await_args.kwargs
    assert kw["event_type"] == "trail_sl_drift_detected"
    assert kw["bot_id"] == "alpha"
    assert kw["correlation_id"] == "trail-audit-alpha-BTCUSDT-42"
    assert kw["occurred_at"] == _FIXED_NOW
    payload = kw["payload"]
    # L-011/L-013 — payload provably JSON-native.
    assert isinstance(payload["trade_id"], int)
    assert isinstance(payload["bot_id"], str)
    assert isinstance(payload["expected_sl_price"], str)
    assert isinstance(payload["observed_sl_price"], str)
    assert isinstance(payload["drift_pct"], str)
    assert isinstance(payload["tolerance_pct"], str)
    assert payload["expected_sl_price"] == "99.500"
    assert payload["observed_sl_price"] == "99.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("sl", ["99.45", "99.5"])
async def test_trail_audit_no_emit_within_tolerance_or_exact(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
    sl: str,
) -> None:
    """expected 99.5; exchange 99.45 → drift 0.05/99.5≈0.000502 ≤ 0.001;
    exchange 99.5 → drift 0 → neither emits.
    """
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT")]
    adapters = _adapters(alpha=_adapter([_pos("BTCUSDT", sl_price=sl)]))
    await _tick(adapters=adapters)
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_boundary_drift_equals_tolerance_not_emitted(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """Strict `>`: trail_pct=0 → expected 100; exchange 100.1 →
    drift 0.1/100 == 0.001 == tolerance → NOT emitted (<=).
    """
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT")]
    patched_fsm.return_value = {
        "be_trigger": Decimal("0.005"),
        "be_sl_level": Decimal("0.003"),
        "trail_pct": Decimal("0"),
    }
    adapters = _adapters(alpha=_adapter([_pos("BTCUSDT", sl_price="100.1")]))
    await _tick(adapters=adapters, tolerance="0.001")
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_sell_side_drift_emits(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """sell best=100 trail_pct=0.005 → expected 100.5; exchange 100.61 →
    drift 0.11/100.5≈0.0010945 > 0.001 → emit (sign/formula correctness).
    """
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT", side="sell")]
    adapters = _adapters(
        alpha=_adapter([_pos("BTCUSDT", sl_price="100.61", side="sell")]),
    )
    await _tick(adapters=adapters)
    patched_insert.assert_awaited_once()
    assert patched_insert.await_args is not None
    assert patched_insert.await_args.kwargs["payload"]["expected_sl_price"] == "100.500"


@pytest.mark.asyncio
async def test_trail_audit_non_trail_skipped(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """sl_type != 'trail' → no fsm read, no get_positions, no emit."""
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT", sl_type="protective")]
    adapter = _adapter([_pos("BTCUSDT", sl_price="50.0")])
    await _tick(adapters=_adapters(alpha=adapter))
    patched_fsm.assert_not_awaited()
    adapter.get_positions.assert_not_awaited()
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_best_price_none_skipped(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """sl_type='trail' but best_price None → skipped before fsm read."""
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT", best_price=None)]
    await _tick(adapters=_adapters(alpha=_adapter([_pos("BTCUSDT")])))
    patched_fsm.assert_not_awaited()
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_fsm_none_skipped(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """select_trade_fsm_params None → candidate dropped, no emit."""
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT")]
    patched_fsm.return_value = None
    await _tick(adapters=_adapters(alpha=_adapter([_pos("BTCUSDT", sl_price="50")])))
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_pos_none_or_flat_skipped(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """No matching exchange position, or size==0 → no emit."""
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT")]
    await _tick(adapters=_adapters(alpha=_adapter([])))
    patched_insert.assert_not_awaited()

    await _tick(
        adapters=_adapters(
            alpha=_adapter([_pos("BTCUSDT", size="0", sl_price="50")]),
        ),
    )
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_exchange_sl_none_skipped(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """exchange_sl None = SL removed → T-534b2/H-028 domain, no emit."""
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT")]
    await _tick(
        adapters=_adapters(alpha=_adapter([_pos("BTCUSDT", sl_price=None)])),
    )
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
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
async def test_trail_audit_skipped_on_get_positions_error(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
    exc: Exception,
) -> None:
    """Transient/failed get_positions = uncertainty, NEVER a drift emit."""
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT")]
    await _tick(adapters=_adapters(alpha=_adapter(exc)))
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_expected_le_zero_guard(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """trail_pct=1, buy → expected 100*(1-1)=0 → div-by-zero guard skips."""
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT")]
    patched_fsm.return_value = {
        "be_trigger": Decimal("0.005"),
        "be_sl_level": Decimal("0.003"),
        "trail_pct": Decimal("1"),
    }
    await _tick(
        adapters=_adapters(alpha=_adapter([_pos("BTCUSDT", sl_price="50")])),
    )
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_skips_paper_bots(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """Paper bot skipped — no DB roundtrip, no emit (OQ-3=A live-only)."""
    paper_adapter = _adapter([_pos("BTCUSDT", sl_price="50")])
    await _tick(
        adapters=_adapters(paperbot=paper_adapter),
        paper=_paper("paperbot"),
    )
    patched_ps.assert_not_awaited()
    paper_adapter.get_positions.assert_not_awaited()
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_no_live_bots_no_op(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """All bots paper → no-op, no DB roundtrip."""
    await _tick(
        adapters=_adapters(p1=_adapter([]), p2=_adapter([])),
        paper=_paper("p1", "p2"),
    )
    patched_ps.assert_not_awaited()
    patched_insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_trail_audit_expected_quantized_no_false_drift(
    patched_ps: AsyncMock,
    patched_fsm: AsyncMock,
    patched_insert: AsyncMock,
) -> None:
    """T-558b2 / H-038 — quantized `expected` vs quantized exchange SL → drift 0, NO false-positive.

    buy best=10 trail_pct=0.005 → raw _compute_trail_sl_price = 9.950; tick 0.1 →
    quantize buy ROUND_FLOOR → 9.9 (the value lifecycle now sends / Bybit holds).
    Pre-T-558b2 raw expected 9.950 vs exchange 9.9 → drift 0.05/9.950 ≈ 0.005025
    > 0.001 tolerance → false `trail_sl_drift_detected`. With `expected` quantized
    → 9.9 == 9.9 → drift 0 → NO emit.
    """
    patched_ps.return_value = [_ps_trail("alpha", "BTCUSDT", best_price="10")]
    adapters = _adapters(alpha=_adapter([_pos("BTCUSDT", sl_price="9.9")], tick="0.1"))
    await _tick(adapters=adapters)
    patched_insert.assert_not_awaited()
