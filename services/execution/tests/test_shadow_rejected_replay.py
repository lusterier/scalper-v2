"""§N4 unit tests for :mod:`services.execution.app.shadow_rejected_replay` (T-513b1).

Pin BRIEF §13.5 / §20 H-023 invariants for rejected-signal observation
restart-recovery via OHLC replay:

* Replay determinism — same input candles → same outcome (BRIEF §13.7 unit test).
* Terminal detection during replay via terminal_future.done() post-each-candle.
* Wall-clock timer carry-over (created_at + observation_minutes).
* SHUTDOWN_MID_REPLAY outcome on window-cap exceeded + per-task compute timeout.
* virtual_entry == 0 defensive early-return (mirror T-513a precedent).
* BE-trigger restart deficiency: replay re-iterates from created_at; sticky
  flag re-asserted by deterministic OHLC reproduction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from packages.core.types import ShadowRejectedTerminal
from packages.db.queries.market_data import OhlcReplayRow
from packages.db.queries.shadow import ShadowRejectedRow
from services.execution.app.config import Settings
from services.execution.app.shadow_rejected_replay import (
    _decode_meta,
    replay_rejected_observation_to_now,
    resume_active_observations_on_startup,
)
from services.execution.app.shadow_rejected_worker import ShadowRejectedWorker

_FIXED_NOW = datetime(2026, 5, 8, 12, 50, tzinfo=UTC)
_REJECTED_CREATED_AT = datetime(2026, 5, 8, 12, 20, tzinfo=UTC)  # 30 min ago


def _make_settings() -> Settings:
    """Settings with shadow_rejected_replay_* defaults; bypass env reads."""
    return Settings(
        database_url="postgresql://test",
    )


def _make_pool_mock() -> MagicMock:
    """asyncpg.Pool stand-in for replay path (no parent state check needed)."""
    pool = MagicMock()
    conn = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _make_rejected_row(
    *,
    row_id: int = 1,
    bot_id: str = "alpha",
    symbol: str = "BTCUSDT",
    would_side: str = "buy",
    created_at: datetime | None = None,
    meta: dict[str, Any] | None = None,
) -> ShadowRejectedRow:
    """Synthetic ShadowRejectedRow with T-513a meta payload."""
    if meta is None:
        meta = {
            "virtual_entry_price": "65000",
            "sl_pct": "0.005",
            "tp_pct": "0.01",
            "be_trigger": "0.005",
            "be_sl_level": "0",
        }
    return ShadowRejectedRow(
        id=row_id,
        signal_id=100 + row_id,
        bot_id=bot_id,
        symbol=symbol,
        would_side=would_side,
        created_at=created_at or _REJECTED_CREATED_AT,
        terminated_at=None,
        terminal_outcome=None,
        mfe_pct=None,
        mae_pct=None,
        meta=meta,
    )


def _make_shadow_rejected_worker() -> ShadowRejectedWorker:
    bus = MagicMock()
    bus.subscribe = AsyncMock(return_value=MagicMock(active=True))
    bus.publish = AsyncMock()
    pool = MagicMock()
    return ShadowRejectedWorker(
        bus=bus,
        pool=pool,
        observation_minutes=60,
        clock=lambda: _FIXED_NOW,
    )


def _make_ohlc_row(
    *,
    bucket_start: datetime,
    open_: str = "65000",
    high: str = "65100",
    low: str = "64900",
    close: str = "65050",
    volume: str = "1",
) -> OhlcReplayRow:
    return OhlcReplayRow(
        bucket_start=bucket_start,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def _make_async_cursor(rows: list[OhlcReplayRow]) -> Any:
    """Build an async-iterator factory matching select_ohlc_for_replay_window signature."""

    async def _cursor(conn: Any, **kwargs: Any) -> AsyncIterator[OhlcReplayRow]:
        for row in rows:
            yield row

    return _cursor


# ---------------------------------------------------------------------------
# _decode_meta — T-513a meta JSONB round-trip
# ---------------------------------------------------------------------------


def test_decode_meta_round_trips_decimal_via_str() -> None:
    """T-513b1: meta values are str(Decimal) at insert (T-513a); decoded back to Decimal."""
    meta = {
        "virtual_entry_price": "65000",
        "sl_pct": "0.007",
        "tp_pct": "0.015",
        "be_trigger": "0.003",
        "be_sl_level": "0.001",
    }
    decoded = _decode_meta(meta)
    assert decoded["virtual_entry_price"] == Decimal("65000")
    assert decoded["sl_pct"] == Decimal("0.007")
    assert decoded["tp_pct"] == Decimal("0.015")
    assert decoded["be_trigger"] == Decimal("0.003")
    assert decoded["be_sl_level"] == Decimal("0.001")


def test_decode_meta_handles_legacy_empty_meta() -> None:
    """Legacy rows (pre-T-513a meta extension) have empty meta — defaults fill (entry==0)."""
    decoded = _decode_meta({})
    assert decoded["virtual_entry_price"] == Decimal("0")
    assert decoded["sl_pct"] == Decimal("0.005")
    assert decoded["be_trigger"] == Decimal("0")


# ---------------------------------------------------------------------------
# replay_rejected_observation_to_now — finalize / skip / window-exceeded paths
# ---------------------------------------------------------------------------


async def test_replay_writes_shutdown_mid_replay_when_window_cap_exceeded() -> None:
    """WG#7: created_at > 48h ago → SHUTDOWN_MID_REPLAY (window cap pre-check; no cursor open)."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    # 50h ago > 48h cap.
    row = _make_rejected_row(created_at=_FIXED_NOW - timedelta(hours=50))

    update_mock = AsyncMock(return_value=_make_rejected_row())
    with patch(
        "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
        update_mock,
    ):
        await replay_rejected_observation_to_now(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            row=row,
            observation_minutes=60,
            clock=lambda: _FIXED_NOW,
        )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert (
        update_mock.await_args.kwargs["terminal_outcome"]
        == ShadowRejectedTerminal.SHUTDOWN_MID_REPLAY
    )


async def test_replay_writes_no_trigger_when_observation_minutes_already_elapsed() -> None:
    """WG#8: created_at + observation_minutes <= now → NO_TRIGGER (cannot recover sticky)."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    # 90 min ago with observation_minutes=60 → expired 30 min ago.
    expired_created_at = _FIXED_NOW - timedelta(minutes=90)
    row = _make_rejected_row(created_at=expired_created_at)

    update_mock = AsyncMock(return_value=_make_rejected_row())
    with patch(
        "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
        update_mock,
    ):
        await replay_rejected_observation_to_now(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            row=row,
            observation_minutes=60,
            clock=lambda: _FIXED_NOW,
        )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.NO_TRIGGER


async def test_replay_writes_no_trigger_when_virtual_entry_zero() -> None:
    """WG#9: virtual_entry == 0 (cold-start fallback) → NO_TRIGGER without subscribe."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    row = _make_rejected_row(
        meta={
            "virtual_entry_price": "0",
            "sl_pct": "0.005",
            "tp_pct": "0.01",
            "be_trigger": "0",
            "be_sl_level": "0",
        },
    )

    update_mock = AsyncMock(return_value=_make_rejected_row())
    with patch(
        "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
        update_mock,
    ):
        await replay_rejected_observation_to_now(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            row=row,
            observation_minutes=60,
            clock=lambda: _FIXED_NOW,
        )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.NO_TRIGGER


async def test_replay_finalizes_would_sl_when_sl_threshold_crossed_during_replay() -> None:
    """Replay-finalize path: candle low crosses SL threshold (long buy) → WOULD_SL."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    # entry=65000, sl_pct=0.005 → sl_threshold=64675; candle low=64600 < 64675 triggers.
    row = _make_rejected_row()
    candles = [
        _make_ohlc_row(
            bucket_start=_REJECTED_CREATED_AT + timedelta(minutes=1),
            high="65000",
            low="64600",
            close="64700",
        ),
    ]

    update_mock = AsyncMock(return_value=_make_rejected_row())
    with (
        patch(
            "services.execution.app.shadow_rejected_replay.select_ohlc_for_replay_window",
            _make_async_cursor(candles),
        ),
        patch(
            "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
            update_mock,
        ),
    ):
        await replay_rejected_observation_to_now(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            row=row,
            observation_minutes=60,
            clock=lambda: _FIXED_NOW,
        )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_SL
    # MFE/MAE non-null — computed from obs_state best/worst per T-513a helper.
    assert update_mock.await_args.kwargs["mfe_pct"] is not None
    assert update_mock.await_args.kwargs["mae_pct"] is not None


async def test_replay_finalizes_would_tp_when_tp_threshold_crossed_during_replay() -> None:
    """Replay-finalize path: candle high crosses TP threshold (long buy) → WOULD_TP."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    # entry=65000, tp_pct=0.01 → tp_threshold=65650; candle high=65700 > 65650 triggers.
    row = _make_rejected_row()
    candles = [
        _make_ohlc_row(
            bucket_start=_REJECTED_CREATED_AT + timedelta(minutes=2),
            high="65700",
            low="65000",
            close="65500",
        ),
    ]

    update_mock = AsyncMock(return_value=_make_rejected_row())
    with (
        patch(
            "services.execution.app.shadow_rejected_replay.select_ohlc_for_replay_window",
            _make_async_cursor(candles),
        ),
        patch(
            "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
            update_mock,
        ),
    ):
        await replay_rejected_observation_to_now(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            row=row,
            observation_minutes=60,
            clock=lambda: _FIXED_NOW,
        )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_TP


async def test_replay_spawns_live_continuation_when_window_exhausted_no_terminal() -> None:
    """WG#12: no triggering candles + window not yet exhausted → register live continuation task."""
    pool = _make_pool_mock()
    bus = MagicMock()
    bus.subscribe = AsyncMock(return_value=MagicMock(active=True))
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    row = _make_rejected_row()
    # No-trigger candles: prices stay in [64850, 65150]; SL=64675/TP=65650 not crossed.
    candles = [
        _make_ohlc_row(
            bucket_start=_REJECTED_CREATED_AT + timedelta(minutes=i),
            high="65150",
            low="64850",
            close="65050",
        )
        for i in range(5)
    ]

    update_mock = AsyncMock(return_value=_make_rejected_row())
    with (
        patch(
            "services.execution.app.shadow_rejected_replay.select_ohlc_for_replay_window",
            _make_async_cursor(candles),
        ),
        patch(
            "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
            update_mock,
        ),
    ):
        await replay_rejected_observation_to_now(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            row=row,
            observation_minutes=60,
            clock=lambda: _FIXED_NOW,
        )
    # No finalize during replay → spawned live continuation task.
    update_mock.assert_not_awaited()
    assert row.id in worker._active_tasks
    # Cleanup: cancel the spawned task to drain.
    task = worker._active_tasks[row.id]
    task.cancel()


async def test_replay_per_observation_compute_timeout_finalizes_shutdown_mid_replay() -> None:
    """WG#10: per-task compute timeout fires → SHUTDOWN_MID_REPLAY."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    # Set timeout to 0.0001s; any cursor body call will trip TimeoutError.
    settings.shadow_rejected_replay_per_observation_timeout_seconds = 0.0001
    worker = _make_shadow_rejected_worker()
    row = _make_rejected_row()

    async def _slow_cursor(conn: Any, **kwargs: Any) -> AsyncIterator[OhlcReplayRow]:
        import asyncio

        await asyncio.sleep(0.5)  # exceeds timeout deliberately
        yield _make_ohlc_row(bucket_start=_REJECTED_CREATED_AT + timedelta(minutes=1))

    update_mock = AsyncMock(return_value=_make_rejected_row())
    with (
        patch(
            "services.execution.app.shadow_rejected_replay.select_ohlc_for_replay_window",
            _slow_cursor,
        ),
        patch(
            "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
            update_mock,
        ),
    ):
        await replay_rejected_observation_to_now(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            row=row,
            observation_minutes=60,
            clock=lambda: _FIXED_NOW,
        )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert (
        update_mock.await_args.kwargs["terminal_outcome"]
        == ShadowRejectedTerminal.SHUTDOWN_MID_REPLAY
    )


async def test_replay_short_side_sl_finalize_when_high_crosses_sl_threshold() -> None:
    """Short-side symmetry: candle high crosses SL threshold → WOULD_SL."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    # Short: sl_threshold = entry * (1 + 0.005) = 65325; candle high=65400 > 65325 triggers.
    row = _make_rejected_row(would_side="sell")
    candles = [
        _make_ohlc_row(
            bucket_start=_REJECTED_CREATED_AT + timedelta(minutes=1),
            high="65400",
            low="65000",
            close="65300",
        ),
    ]

    update_mock = AsyncMock(return_value=_make_rejected_row())
    with (
        patch(
            "services.execution.app.shadow_rejected_replay.select_ohlc_for_replay_window",
            _make_async_cursor(candles),
        ),
        patch(
            "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
            update_mock,
        ),
    ):
        await replay_rejected_observation_to_now(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            row=row,
            observation_minutes=60,
            clock=lambda: _FIXED_NOW,
        )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_SL


# ---------------------------------------------------------------------------
# Hand-verification timer math (BRIEF §13.5 wall-clock carry-over)
# ---------------------------------------------------------------------------


def test_wall_clock_carry_over_timer_math() -> None:
    """OQ-3=A mirror: observation created at T_0 + observation_minutes=60.

    Restart at T_0 + 130 minutes → expires_at = T_0 + 60min;
    remaining_seconds = (T_0+60min - T_0-130min) = -4200s → triggers timer-elapsed pre-check.
    """
    t_0 = datetime(2026, 5, 8, 11, 0, tzinfo=UTC)
    t_restart = t_0 + timedelta(minutes=130)
    observation_minutes = 60
    expires_at = t_0 + timedelta(minutes=observation_minutes)
    remaining_seconds = (expires_at - t_restart).total_seconds()
    assert remaining_seconds == pytest.approx(-4200.0)
    assert expires_at == datetime(2026, 5, 8, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# resume_active_observations_on_startup — enumeration + per-observation isolation
# ---------------------------------------------------------------------------


async def test_resume_startup_iterates_all_active_observations() -> None:
    """resume hook enumerates select_all_active_shadow_rejected + dispatches per-row."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    rows = [_make_rejected_row(row_id=1), _make_rejected_row(row_id=2)]

    replay_mock = AsyncMock()
    with (
        patch(
            "services.execution.app.shadow_rejected_replay.select_all_active_shadow_rejected",
            AsyncMock(return_value=rows),
        ),
        patch(
            "services.execution.app.shadow_rejected_replay.replay_rejected_observation_to_now",
            replay_mock,
        ),
    ):
        await resume_active_observations_on_startup(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            clock=lambda: _FIXED_NOW,
        )
    assert replay_mock.await_count == 2


async def test_resume_startup_per_observation_failure_does_not_block_others() -> None:
    """Best-effort isolation: one failed observation logged + continue."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    rows = [
        _make_rejected_row(row_id=1),
        _make_rejected_row(row_id=2),
        _make_rejected_row(row_id=3),
    ]

    call_count = 0

    async def _flaky_replay(*args: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            msg = "synthetic per-observation failure"
            raise RuntimeError(msg)

    with (
        patch(
            "services.execution.app.shadow_rejected_replay.select_all_active_shadow_rejected",
            AsyncMock(return_value=rows),
        ),
        patch(
            "services.execution.app.shadow_rejected_replay.replay_rejected_observation_to_now",
            _flaky_replay,
        ),
    ):
        await resume_active_observations_on_startup(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            clock=lambda: _FIXED_NOW,
        )
    # 3 rows, 1 failed mid-loop, total still 3 attempted (no early exit).
    assert call_count == 3


async def test_resume_startup_passes_observation_minutes_from_settings() -> None:
    """Settings.shadow_rejected_observation_minutes threaded to per-row replay."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    settings.shadow_rejected_observation_minutes = 90
    worker = _make_shadow_rejected_worker()
    rows = [_make_rejected_row(row_id=1)]

    replay_mock = AsyncMock()
    with (
        patch(
            "services.execution.app.shadow_rejected_replay.select_all_active_shadow_rejected",
            AsyncMock(return_value=rows),
        ),
        patch(
            "services.execution.app.shadow_rejected_replay.replay_rejected_observation_to_now",
            replay_mock,
        ),
    ):
        await resume_active_observations_on_startup(
            pool=pool,
            bus=bus,
            settings=settings,
            shadow_rejected_worker=worker,
            clock=lambda: _FIXED_NOW,
        )
    assert replay_mock.await_args is not None
    assert replay_mock.await_args.kwargs["observation_minutes"] == 90


# ---------------------------------------------------------------------------
# Replay determinism (BRIEF §13.7 invariant)
# ---------------------------------------------------------------------------


async def test_replay_determinism_same_input_yields_same_outcome() -> None:
    """Same OHLC sequence + same seed_state → same outcome on repeated invocation."""
    pool = _make_pool_mock()
    bus = MagicMock()
    settings = _make_settings()
    worker = _make_shadow_rejected_worker()
    row = _make_rejected_row()
    candles = [
        _make_ohlc_row(
            bucket_start=_REJECTED_CREATED_AT + timedelta(minutes=1),
            high="65000",
            low="64600",
            close="64700",
        ),
    ]

    outcomes: list[ShadowRejectedTerminal] = []
    for _ in range(2):
        update_mock = AsyncMock(return_value=_make_rejected_row())
        with (
            patch(
                "services.execution.app.shadow_rejected_replay.select_ohlc_for_replay_window",
                _make_async_cursor(candles),
            ),
            patch(
                "services.execution.app.shadow_rejected_replay.update_shadow_rejected_terminal",
                update_mock,
            ),
        ):
            await replay_rejected_observation_to_now(
                pool=pool,
                bus=bus,
                settings=settings,
                shadow_rejected_worker=worker,
                row=row,
                observation_minutes=60,
                clock=lambda: _FIXED_NOW,
            )
        assert update_mock.await_args is not None
        outcomes.append(update_mock.await_args.kwargs["terminal_outcome"])
    # Both runs converge on the same outcome.
    assert outcomes[0] == outcomes[1] == ShadowRejectedTerminal.WOULD_SL


# ---------------------------------------------------------------------------
# Hand-verification threshold + MFE/MAE math (T-513a precedent reuse)
# ---------------------------------------------------------------------------


def test_threshold_computation_long_side_matches_t513a_precedent() -> None:
    """Plan §Hand verification: entry=65000, sl=0.005, tp=0.01, be=0.005 → 64675/65650/65325."""
    from services.execution.app.shadow_rejected_worker import _compute_thresholds

    tp, sl, be = _compute_thresholds(
        side="buy",
        entry=Decimal("65000"),
        sl_pct=Decimal("0.005"),
        tp_pct=Decimal("0.01"),
        be_trigger=Decimal("0.005"),
    )
    assert tp == Decimal("65650.000")
    assert sl == Decimal("64675.000")
    assert be == Decimal("65325.000")


def test_mfe_mae_computation_long_side_matches_t513a_precedent() -> None:
    """Plan §Hand verification: best=65500, worst=64900 → mfe≈0.00769, mae≈0.00154."""
    from services.execution.app.shadow_rejected_worker import _compute_mfe_mae_pcts

    mfe, mae = _compute_mfe_mae_pcts(
        side="buy",
        entry=Decimal("65000"),
        best=Decimal("65500"),
        worst=Decimal("64900"),
    )
    assert mfe == pytest.approx(500 / 65000)
    assert mae == pytest.approx(100 / 65000)
