"""§N4 unit tests for :mod:`services.execution.app.shadow_rejected_worker` (T-513a).

Pin BRIEF §13.5 invariants:

* 4-outcome enum (would_tp / would_sl / would_be / no_trigger) per side.
* SL-first conservative bias on same-candle TP+SL race (per WG#4).
* BE-trigger sticky flag (per WG#5; >= inequality boundary).
* entry==0 defensive early-return → NO_TRIGGER without subscribe + 60-min wait
  (per pass-2 CONCERN fix).
* H-016 finalizer: try/finally bus_unsubscribe + registry cleanup.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from packages.bus import MessageEnvelope
from packages.bus.payloads import ShadowRejectedStartPayload
from packages.bus.schemas import OhlcCandlePayload
from packages.core import CorrelationId
from packages.core.types import ShadowRejectedTerminal
from packages.db.queries.shadow import ShadowRejectedRow
from services.execution.app.shadow_rejected_worker import (
    ShadowRejectedWorker,
    _compute_mfe_mae_pcts,
    _compute_thresholds,
)

_FIXED_NOW = datetime(2026, 5, 8, 12, 0, tzinfo=UTC)


def _make_pool() -> MagicMock:
    pool = MagicMock()
    conn = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _make_bus() -> tuple[MagicMock, list[tuple[str, Any]]]:
    subs_log: list[tuple[str, Any]] = []

    async def fake_subscribe(subject: str, handler: Any) -> MagicMock:
        sub = MagicMock(spec=["active", "unsubscribe"])
        sub.active = True
        sub.unsubscribe = AsyncMock()
        subs_log.append((subject, handler))
        return sub

    bus = MagicMock()
    bus.subscribe = fake_subscribe
    bus.publish = AsyncMock()
    return bus, subs_log


def _make_worker(*, observation_minutes: int = 60) -> ShadowRejectedWorker:
    bus, _ = _make_bus()
    return ShadowRejectedWorker(
        bus=bus,
        pool=_make_pool(),
        observation_minutes=observation_minutes,
        clock=lambda: _FIXED_NOW,
    )


def _make_payload(
    *,
    action: str = "LONG",
    virtual_entry_price: Decimal = Decimal("65000"),
    sl_pct: Decimal = Decimal("0.005"),
    tp_pct: Decimal = Decimal("0.01"),
    be_trigger: Decimal = Decimal("0.005"),
    be_sl_level: Decimal = Decimal("0.001"),
) -> ShadowRejectedStartPayload:
    return ShadowRejectedStartPayload(
        signal_id=42,
        bot_id="alpha",
        symbol="BTCUSDT",
        action=action,  # type: ignore[arg-type]
        virtual_entry_price=virtual_entry_price,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        be_trigger=be_trigger,
        be_sl_level=be_sl_level,
        rejected_at=_FIXED_NOW,
    )


def _make_rejected_row() -> ShadowRejectedRow:
    return ShadowRejectedRow(
        id=1,
        signal_id=42,
        bot_id="alpha",
        symbol="BTCUSDT",
        would_side="buy",
        created_at=_FIXED_NOW,
        terminated_at=None,
        terminal_outcome=None,
        mfe_pct=None,
        mae_pct=None,
        meta={},
    )


def _candle_envelope(
    *,
    high: Decimal,
    low: Decimal,
    close: Decimal | None = None,
    open_: Decimal | None = None,
    is_closed: bool = True,
) -> MessageEnvelope:
    payload = OhlcCandlePayload(
        symbol="BTCUSDT",
        bucket_start=datetime(2026, 5, 8, 12, 1, tzinfo=UTC),
        open=open_ or close or low,
        high=high,
        low=low,
        close=close or high,
        volume=Decimal("100"),
        is_closed=is_closed,
    )
    return MessageEnvelope(
        correlation_id=CorrelationId("test-corr"),
        publisher="test",
        payload=payload.model_dump(mode="json"),
    )


async def _drive_observation(
    worker: ShadowRejectedWorker,
    payload: ShadowRejectedStartPayload,
    candles: list[MessageEnvelope],
    *,
    observation_seconds_override: float | None = None,
) -> tuple[AsyncMock, list[tuple[str, Any]]]:
    """Run _on_rejected_start + drive candles + return (update_mock, subs_log).

    Candles are delivered SYNCHRONOUSLY inside the fake ``subscribe`` call so the
    candle handler updates ``obs_state`` (be_triggered, best/worst, terminal_future)
    BEFORE the task hits ``asyncio.wait_for(... timeout=0)``. This avoids a
    race where ``timeout=0`` short-circuits before tests can push candles.
    """
    subs_log: list[tuple[str, Any]] = []

    async def fake_subscribe(subject: str, handler: Any) -> MagicMock:
        sub = MagicMock(spec=["active", "unsubscribe"])
        sub.active = True
        sub.unsubscribe = AsyncMock()
        subs_log.append((subject, handler))
        if subject.startswith("market.ohlc.1m."):
            for candle in candles:
                await handler(candle)
        return sub

    bus = MagicMock()
    bus.subscribe = fake_subscribe
    bus.publish = AsyncMock()
    worker._bus = bus

    insert_mock = AsyncMock(return_value=_make_rejected_row())
    update_mock = AsyncMock(return_value=_make_rejected_row())
    if observation_seconds_override is not None:
        worker._observation_minutes = max(1, int(observation_seconds_override / 60))
    with (
        patch(
            "services.execution.app.shadow_rejected_worker.insert_shadow_rejected",
            insert_mock,
        ),
        patch(
            "services.execution.app.shadow_rejected_worker.update_shadow_rejected_terminal",
            update_mock,
        ),
    ):
        await worker._on_rejected_start(payload)
        # Wait for tasks to complete.
        if 1 in worker._active_tasks:
            try:
                await asyncio.wait_for(worker._active_tasks[1], timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                worker._active_tasks[1].cancel()
    return update_mock, subs_log


# ---------------------------------------------------------------------------
# Pure-function tests (worker-test #1 — _compute_thresholds + _compute_mfe_mae)
# ---------------------------------------------------------------------------


def test_compute_thresholds_long_math() -> None:
    """Long entry=65000, sl_pct=0.005, tp_pct=0.01, be_trigger=0.005 →
    TP=65650, SL=64675, BE=65325."""
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


def test_compute_thresholds_short_math() -> None:
    """Short entry=65000, sl_pct=0.005, tp_pct=0.01, be_trigger=0.005 →
    TP=64350, SL=65325, BE=64675."""
    tp, sl, be = _compute_thresholds(
        side="sell",
        entry=Decimal("65000"),
        sl_pct=Decimal("0.005"),
        tp_pct=Decimal("0.01"),
        be_trigger=Decimal("0.005"),
    )
    assert tp == Decimal("64350.000")
    assert sl == Decimal("65325.000")
    assert be == Decimal("64675.000")


def test_compute_thresholds_with_zero_entry_returns_zeros() -> None:
    """Math safety: entry=0 → all thresholds 0 (caller-side guard required)."""
    tp, sl, be = _compute_thresholds(
        side="buy",
        entry=Decimal("0"),
        sl_pct=Decimal("0.005"),
        tp_pct=Decimal("0.01"),
        be_trigger=Decimal("0.005"),
    )
    assert tp == Decimal("0")
    assert sl == Decimal("0")
    assert be == Decimal("0")


def test_compute_mfe_mae_pcts_long() -> None:
    """Long entry=65000, best=65500, worst=64900 →
    MFE = (65500-65000)/65000 ≈ 0.00769; MAE = (65000-64900)/65000 ≈ 0.00154."""
    mfe, mae = _compute_mfe_mae_pcts(
        side="buy",
        entry=Decimal("65000"),
        best=Decimal("65500"),
        worst=Decimal("64900"),
    )
    assert mfe == 500.0 / 65000.0
    assert mae == 100.0 / 65000.0


def test_compute_mfe_mae_pcts_zero_entry_returns_zeros() -> None:
    """Defensive: entry=0 → (0.0, 0.0); no DivisionByZero."""
    mfe, mae = _compute_mfe_mae_pcts(
        side="buy",
        entry=Decimal("0"),
        best=Decimal("100"),
        worst=Decimal("50"),
    )
    assert mfe == 0.0
    assert mae == 0.0


# ---------------------------------------------------------------------------
# Observation FSM tests (worker-test #6-#16)
# ---------------------------------------------------------------------------


async def test_observation_terminates_on_sl_cross_long() -> None:
    """Long entry=65000, sl_pct=0.005 → SL=64675; candle low=64670 → WOULD_SL."""
    worker = _make_worker()
    payload = _make_payload()
    update_mock, _ = await _drive_observation(
        worker,
        payload,
        candles=[
            _candle_envelope(high=Decimal("64900"), low=Decimal("64670"), close=Decimal("64680"))
        ],
    )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_SL


async def test_observation_terminates_on_tp_cross_long() -> None:
    """Long entry=65000, tp_pct=0.01 → TP=65650; candle high=65700 → WOULD_TP."""
    worker = _make_worker()
    payload = _make_payload()
    update_mock, _ = await _drive_observation(
        worker,
        payload,
        candles=[
            _candle_envelope(high=Decimal("65700"), low=Decimal("65100"), close=Decimal("65600"))
        ],
    )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_TP


async def test_observation_terminates_on_sl_cross_short() -> None:
    """Short entry=65000, sl_pct=0.005 → SL=65325; candle high=65400 → WOULD_SL."""
    worker = _make_worker()
    payload = _make_payload(action="SHORT")
    update_mock, _ = await _drive_observation(
        worker,
        payload,
        candles=[
            _candle_envelope(high=Decimal("65400"), low=Decimal("65100"), close=Decimal("65300"))
        ],
    )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_SL


async def test_observation_terminates_on_tp_cross_short() -> None:
    """Short entry=65000, tp_pct=0.01 → TP=64350; candle low=64300 → WOULD_TP."""
    worker = _make_worker()
    payload = _make_payload(action="SHORT")
    update_mock, _ = await _drive_observation(
        worker,
        payload,
        candles=[
            _candle_envelope(high=Decimal("64900"), low=Decimal("64300"), close=Decimal("64500"))
        ],
    )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_TP


async def test_observation_sl_first_when_same_candle_tp_and_sl_cross() -> None:
    """SL-first conservative bias per WG#4: high>=tp AND low<=sl in same candle → WOULD_SL."""
    worker = _make_worker()
    payload = _make_payload()
    update_mock, _ = await _drive_observation(
        worker,
        payload,
        candles=[
            # high=65700 (>= TP 65650) AND low=64670 (<= SL 64675) — both cross.
            _candle_envelope(high=Decimal("65700"), low=Decimal("64670"), close=Decimal("65000"))
        ],
    )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_SL


async def test_observation_zero_entry_classifies_no_trigger_immediately() -> None:
    """entry==0 → NO_TRIGGER finalize WITHOUT bus.subscribe + WITHOUT 60-min wait."""
    worker = _make_worker()
    payload = _make_payload(virtual_entry_price=Decimal("0"))
    update_mock, subs_log = await _drive_observation(worker, payload, candles=[])
    # No market.ohlc subscription at all (early-return before subscribe).
    market_subs = [s for s in subs_log if s[0].startswith("market.ohlc.1m.")]
    assert market_subs == []
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.NO_TRIGGER
    assert update_mock.await_args.kwargs["mfe_pct"] == 0.0
    assert update_mock.await_args.kwargs["mae_pct"] == 0.0


async def test_be_trigger_fires_at_exact_boundary_long() -> None:
    """Long entry=65000, be_trigger=0.005 → be_threshold=65325; candle high=65325 (exact) →
    be_triggered=True; window elapses → WOULD_BE."""
    worker = _make_worker(observation_minutes=1)
    payload = _make_payload(be_trigger=Decimal("0.005"))
    # Tighten observation timeout so test doesn't hang. Stub pure-function helpers
    # by monkeypatching observation_minutes via worker.
    worker._observation_minutes = 0  # 0 minutes = 0 seconds; immediate timeout
    update_mock, _ = await _drive_observation(
        worker,
        payload,
        candles=[
            # high=65325 exact boundary; low=64900 above SL (64675). No TP, no SL.
            _candle_envelope(high=Decimal("65325"), low=Decimal("64900"), close=Decimal("65200"))
        ],
    )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_BE


async def test_be_trigger_fires_at_exact_boundary_short() -> None:
    """Short entry=65000, be_trigger=0.005 → be_threshold=64675; candle low=64675 (exact)."""
    worker = _make_worker(observation_minutes=1)
    payload = _make_payload(action="SHORT", be_trigger=Decimal("0.005"))
    worker._observation_minutes = 0  # immediate timeout
    update_mock, _ = await _drive_observation(
        worker,
        payload,
        candles=[
            _candle_envelope(high=Decimal("65100"), low=Decimal("64675"), close=Decimal("64800"))
        ],
    )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.WOULD_BE


async def test_observation_no_threshold_within_window_classifies_no_trigger() -> None:
    """Flat candles around entry; be_trigger never crossed; window elapses → NO_TRIGGER."""
    worker = _make_worker()
    worker._observation_minutes = 0  # immediate timeout
    payload = _make_payload()
    update_mock, _ = await _drive_observation(
        worker,
        payload,
        candles=[
            # flat: high=65010, low=64990 — both within entry±0.001 (well below be_trigger 0.005)
            _candle_envelope(high=Decimal("65010"), low=Decimal("64990"), close=Decimal("65000"))
        ],
    )
    update_mock.assert_awaited_once()
    assert update_mock.await_args is not None
    assert update_mock.await_args.kwargs["terminal_outcome"] == ShadowRejectedTerminal.NO_TRIGGER


async def test_observation_finalizer_unsubscribes_on_exception() -> None:
    """H-016 finalizer: own_sub bus_unsubscribe + registry cleanup on exception mid-observation."""
    import contextlib

    worker = _make_worker()
    bus, _ = _make_bus()
    worker._bus = bus
    insert_mock = AsyncMock(return_value=_make_rejected_row())
    # Update raises mid-finalize → triggers finally block.
    update_mock = AsyncMock(side_effect=RuntimeError("DB blip"))
    payload = _make_payload(virtual_entry_price=Decimal("0"))  # entry==0 short-circuit
    with (
        patch(
            "services.execution.app.shadow_rejected_worker.insert_shadow_rejected",
            insert_mock,
        ),
        patch(
            "services.execution.app.shadow_rejected_worker.update_shadow_rejected_terminal",
            update_mock,
        ),
    ):
        await worker._on_rejected_start(payload)
        # Wait for task; expect exception swallowed by task.
        if 1 in worker._active_tasks:
            with contextlib.suppress(RuntimeError):
                await asyncio.wait_for(worker._active_tasks[1], timeout=1.0)
    # Registry cleanup happened in finally despite the raised exception.
    assert 1 not in worker._active_tasks


async def test_on_rejected_start_inserts_row_and_spawns_task() -> None:
    """T-510b shipped insert_shadow_rejected called with payload fields; task registered."""
    worker = _make_worker()
    payload = _make_payload(virtual_entry_price=Decimal("0"))  # short-circuit fast
    insert_mock = AsyncMock(return_value=_make_rejected_row())
    update_mock = AsyncMock(return_value=_make_rejected_row())
    with (
        patch(
            "services.execution.app.shadow_rejected_worker.insert_shadow_rejected",
            insert_mock,
        ),
        patch(
            "services.execution.app.shadow_rejected_worker.update_shadow_rejected_terminal",
            update_mock,
        ),
    ):
        await worker._on_rejected_start(payload)
        assert 1 in worker._active_tasks
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if 1 in worker._active_tasks:
            await asyncio.wait_for(worker._active_tasks[1], timeout=1.0)
    insert_mock.assert_awaited_once()
    assert insert_mock.await_args is not None
    kwargs = insert_mock.await_args.kwargs
    assert kwargs["signal_id"] == 42
    assert kwargs["bot_id"] == "alpha"
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["would_side"] == "buy"  # action=LONG
    assert kwargs["meta"]["virtual_entry_price"] == "0"
    assert kwargs["meta"]["sl_pct"] == "0.005"


def test_start_subscribes_to_shadow_rejected_start_wildcard() -> None:
    """ShadowRejectedWorker.start() subscribes to shadow.rejected.start.> wildcard."""
    bus, subs_log = _make_bus()
    worker = ShadowRejectedWorker(
        bus=bus,
        pool=_make_pool(),
        observation_minutes=60,
        clock=lambda: _FIXED_NOW,
    )
    asyncio.run(worker.start())
    subjects = [s[0] for s in subs_log]
    assert "shadow.rejected.start.>" in subjects


async def test_stop_cancels_active_observation_tasks() -> None:
    """worker.stop() cancels all in-flight observation tasks; idempotent on second call."""
    worker = _make_worker()
    fake_task = MagicMock()
    fake_task.done = MagicMock(return_value=False)
    fake_task.cancel = MagicMock()
    worker._active_tasks[42] = fake_task
    await worker.stop()
    fake_task.cancel.assert_called_once()
    # Second stop is no-op (registry already cleared).
    await worker.stop()


async def test_register_resume_task_smoke_inserts_into_active_tasks() -> None:
    """T-513b1 — register_resume_task inserts into _active_tasks (1:1 keyed by rejected_id)."""
    worker = _make_worker()
    fake_task = MagicMock(spec=asyncio.Task)
    worker.register_resume_task(rejected_id=99, task=fake_task)
    assert worker._active_tasks[99] is fake_task


async def test_register_resume_task_then_stop_cancels_task() -> None:
    """T-513b1 — registered resume task is cancelled by stop() (mirror live-spawn cleanup)."""
    worker = _make_worker()
    fake_task = MagicMock()
    fake_task.done = MagicMock(return_value=False)
    fake_task.cancel = MagicMock()
    worker.register_resume_task(rejected_id=101, task=fake_task)
    await worker.stop()
    fake_task.cancel.assert_called_once()
