"""T-511b1 ShadowWorker FSM core tests (10 unit tests).

Architecture:
* Real ``PaperExchange`` (T-511a refactored) with mocked bus + mocked pool.
* T-510b shipped ``insert_shadow_variant`` + ``update_shadow_variant_terminal``
  patched at shadow_worker module level for test isolation.
* Candles delivered to PE + own variant handler by directly invoking the
  handlers registered on the mock bus (mirrors T-511a pattern).

Coverage map per plan acceptance §5-§7 + BRIEF §13.3 5-outcome vocab:
* test 1   — pydantic envelope validation
* test 2   — dispatch spawns N variants
* test 3-7 — 5 ShadowVariantTerminal outcomes (sl_hit / be_hit / tp_trail / tp_full / timeout)
* test 8   — set_trading_stop rejects sl_type='trail' (ADR-0005 v2 H-024 guard)
* test 9   — _terminal_from_pe_state truth table (4-row pure-function param test)
* test 10  — BRIEF §20 H-016 verbatim test_shadow_task_unsubscribes_on_exception
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.bus import MessageEnvelope
from packages.bus.payloads import ShadowStartPayload, TradeClosedPayload, VariantSpec
from packages.bus.schemas import OhlcCandlePayload
from packages.core import BotId, CorrelationId
from packages.core.types import ShadowVariantTerminal
from packages.db.queries.shadow import ShadowVariantRow
from packages.exchange.paper import PaperExchange
from services.execution.app.shadow_worker import (
    ShadowWorker,
    _check_be_trigger,
    _compute_be_sl_price,
    _compute_trail_sl_price,
    _terminal_from_pe_state,
)

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.close = AsyncMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=[])
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=conn)
    tx_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx_cm)
    pool_cm = MagicMock()
    pool_cm.__aenter__ = AsyncMock(return_value=conn)
    pool_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=pool_cm)
    return pool


def _make_bus_with_log() -> tuple[MagicMock, list[tuple[str, Any]], list[MagicMock]]:
    """Returns (bus, subscriptions_log, sub_mocks).

    subscriptions_log: list of (subject_pattern, handler) tuples in subscribe order.
    sub_mocks: list of subscription mock objects (for assertions on .unsubscribe / .active).
    """
    subs_log: list[tuple[str, Any]] = []
    sub_mocks: list[MagicMock] = []

    async def fake_subscribe(subject: str, handler: Any) -> MagicMock:
        sub = MagicMock(spec=["active", "unsubscribe"])
        sub.active = True
        sub.unsubscribe = AsyncMock()
        subs_log.append((subject, handler))
        sub_mocks.append(sub)
        return sub

    bus = MagicMock()
    bus.subscribe = fake_subscribe
    bus.publish = AsyncMock()
    bus.close = AsyncMock()
    return bus, subs_log, sub_mocks


def _make_shadow_variant_row(*, id_: int = 1, **kwargs: Any) -> ShadowVariantRow:
    """Synthetic ShadowVariantRow with sane defaults for test fixtures."""
    return ShadowVariantRow(
        id=id_,
        parent_trade_id=kwargs.get("parent_trade_id", 42),
        bot_id=kwargs.get("bot_id", "test-bot"),
        variant_name=kwargs.get("variant_name", "baseline"),
        side=kwargs.get("side", "buy"),
        entry_price=kwargs.get("entry_price", Decimal("65000")),
        qty=kwargs.get("qty", Decimal("1")),
        created_at=kwargs.get("created_at", datetime(2026, 5, 8, 12, 0, tzinfo=UTC)),
        terminated_at=None,
        terminal_outcome=None,
        realized_pnl=None,
        mfe_pct=None,
        mae_pct=None,
        meta={},
        parent_kind=kwargs.get("parent_kind", "live"),
    )


def _make_worker(
    bus: MagicMock,
    pool: MagicMock,
    *,
    clock: datetime | None = None,
) -> ShadowWorker:
    fixed_clock = clock or datetime(2026, 5, 8, 12, 0, tzinfo=UTC)
    return ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
        fee_rate=Decimal("0.0006"),
        clock=lambda: fixed_clock,
    )


def _make_payload(
    *,
    parent_trade_id: int = 42,
    parent_kind: str = "live",
    bot_id: str = "test-bot",
    symbol: str = "BTCUSDT",
    side: str = "buy",
    entry_price: Decimal = Decimal("65000"),
    qty: Decimal = Decimal("1"),
    variants: list[VariantSpec] | None = None,
) -> ShadowStartPayload:
    return ShadowStartPayload(
        parent_trade_id=parent_trade_id,
        parent_kind=parent_kind,  # type: ignore[arg-type]
        bot_id=bot_id,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        entry_price=entry_price,
        qty=qty,
        variants=variants
        or [
            VariantSpec(
                name="baseline", overrides={"sl_pct": Decimal("0.005"), "tp_pct": Decimal("0.01")}
            )
        ],
    )


def _candle_envelope(
    *,
    symbol: str = "BTCUSDT",
    open_: Decimal = Decimal("65000"),
    high: Decimal = Decimal("65000"),
    low: Decimal = Decimal("65000"),
    close: Decimal = Decimal("65000"),
    bucket_start: datetime | None = None,
) -> MessageEnvelope:
    payload = OhlcCandlePayload(
        symbol=symbol,
        bucket_start=bucket_start or datetime(2026, 5, 8, 12, 1, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=Decimal("100"),
        is_closed=True,
    )
    return MessageEnvelope(
        correlation_id=CorrelationId("test-corr"),
        publisher="test",
        payload=payload.model_dump(mode="json"),
    )


async def _drive_candle(
    subs_log: list[tuple[str, Any]],
    candle_subject: str,
    envelope: MessageEnvelope,
) -> None:
    """Invoke every handler whose subject pattern matches the candle subject.

    Both PE._on_candle (subscribed at ``market.ohlc.1m.>`` wildcard) and the
    own variant handler (subscribed at ``market.ohlc.1m.<symbol>``) fire.
    """
    for sub_pattern, handler in subs_log:
        if sub_pattern == "market.ohlc.1m.>" or sub_pattern == candle_subject:
            await handler(envelope)


# ---------------------------------------------------------------------------
# Test 1 — pydantic envelope validation
# ---------------------------------------------------------------------------


def test_shadow_start_envelope_pydantic_validation() -> None:
    payload = ShadowStartPayload(
        parent_trade_id=42,
        parent_kind="live",
        bot_id="test",
        symbol="BTCUSDT",
        side="buy",
        entry_price=Decimal("65000"),
        qty=Decimal("1"),
        variants=[
            VariantSpec(name="v1", overrides={"sl_pct": Decimal("0.005")}),
            VariantSpec(name="v2", overrides={}),
        ],
    )
    raw = payload.model_dump(mode="json")
    parsed = ShadowStartPayload.model_validate(raw)
    assert parsed == payload
    # ConfigDict(extra='forbid') rejects unknown top-level fields.
    with pytest.raises(Exception):
        ShadowStartPayload.model_validate({**raw, "garbage_key": "x"})


# ---------------------------------------------------------------------------
# Test 2 — dispatch spawns N variant tasks
# ---------------------------------------------------------------------------


async def test_dispatch_spawns_n_variants() -> None:
    bus, _, _ = _make_bus_with_log()
    pool = _make_pool()
    worker = _make_worker(bus, pool)
    variants = [VariantSpec(name=f"v{i}", overrides={"sl_pct": Decimal("0.005")}) for i in range(5)]
    payload = _make_payload(variants=variants)
    # Patch insert/update so spawned tasks don't actually run real PE logic.
    with (
        patch(
            "services.execution.app.shadow_worker.insert_shadow_variant",
            AsyncMock(return_value=_make_shadow_variant_row()),
        ),
        patch(
            "services.execution.app.shadow_worker.update_shadow_variant_terminal",
            AsyncMock(return_value=None),
        ),
    ):
        await worker._on_shadow_start(payload)
        assert len(worker._active_tasks[42]) == 5
        # Cleanup: cancel all to avoid pending-task pollution.
        for task in list(worker._active_tasks[42]):
            task.cancel()
        await asyncio.gather(*worker._active_tasks[42], return_exceptions=True)


# ---------------------------------------------------------------------------
# Tests 3-7 — 5 ShadowVariantTerminal outcomes via real PE + driven candles
# ---------------------------------------------------------------------------


async def _run_variant_to_terminal(
    *,
    variant_overrides: dict[str, Decimal | int],
    candles: list[MessageEnvelope],
    side: str = "buy",
    entry_price: Decimal = Decimal("65000"),
) -> tuple[AsyncMock, AsyncMock]:
    """Drive a single variant through ``candles``; return (insert_mock, update_mock)."""
    bus, subs_log, _ = _make_bus_with_log()
    pool = _make_pool()
    worker = _make_worker(bus, pool)
    payload = _make_payload(
        side=side,
        entry_price=entry_price,
        variants=[VariantSpec(name="t", overrides=variant_overrides)],
    )
    insert_mock = AsyncMock(
        return_value=_make_shadow_variant_row(side=side, entry_price=entry_price)
    )
    update_mock = AsyncMock(
        return_value=_make_shadow_variant_row(side=side, entry_price=entry_price)
    )
    with (
        patch("services.execution.app.shadow_worker.insert_shadow_variant", insert_mock),
        patch("services.execution.app.shadow_worker.update_shadow_variant_terminal", update_mock),
    ):
        await worker._on_shadow_start(payload)
        # Yield once so spawned task progresses to subscribe + sets up futures.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        for candle in candles:
            await _drive_candle(subs_log, "market.ohlc.1m.BTCUSDT", candle)
        # Wait for variant tasks to complete.
        await asyncio.gather(*worker._active_tasks.get(42, []), return_exceptions=True)
    return insert_mock, update_mock


async def test_variant_runs_to_sl_hit_terminal() -> None:
    # Long entry 65000, sl_pct=0.005 → initial SL=64675; protective.
    # Drive candle with low=64670 → SL cross.
    _, update_mock = await _run_variant_to_terminal(
        variant_overrides={"sl_pct": Decimal("0.005"), "tp_pct": Decimal("0.01")},
        candles=[
            _candle_envelope(
                open_=Decimal("64900"),
                high=Decimal("64900"),
                low=Decimal("64670"),
                close=Decimal("64680"),
            )
        ],
    )
    assert update_mock.call_count == 1
    kwargs = update_mock.call_args.kwargs
    assert kwargs["terminal_outcome"] == ShadowVariantTerminal.SL_HIT


async def test_variant_runs_to_be_hit_terminal() -> None:
    # Long entry 65000; be_trigger=0.005 → cross at 65325; be_sl_level=0.001 → BE-SL=65065.
    # Candle 1: high=65325 (BE trigger), low=65300 (no SL cross — initial SL=64675 protective).
    # Candle 2: high=65100, low=65060 < 65065 → BE-SL cross.
    _, update_mock = await _run_variant_to_terminal(
        variant_overrides={
            "be_trigger": Decimal("0.005"),
            "be_sl_level": Decimal("0.001"),
            "sl_pct": Decimal("0.005"),
            "tp_pct": Decimal("0.01"),
        },
        candles=[
            _candle_envelope(
                open_=Decimal("65000"),
                high=Decimal("65325"),
                low=Decimal("65300"),
                close=Decimal("65300"),
            ),
            _candle_envelope(
                open_=Decimal("65100"),
                high=Decimal("65100"),
                low=Decimal("65060"),
                close=Decimal("65060"),
                bucket_start=datetime(2026, 5, 8, 12, 2, tzinfo=UTC),
            ),
        ],
    )
    assert update_mock.call_count == 1
    kwargs = update_mock.call_args.kwargs
    assert kwargs["terminal_outcome"] == ShadowVariantTerminal.BE_HIT


async def test_variant_runs_to_tp_trail_terminal() -> None:
    # Long entry 65000; tp_pct=0.01 → TP=65650; tp_qty_pct=0.2 → Partial.
    # trail_pct=0.003 → after partial TP, sl_type='trail' + trail SL = 65650 * 0.997 = 65453.05.
    # Candle 1: high=65650, low=65500 → partial TP cross → sl_type='trail'.
    # Candle 2: low=65450 < 65453.05 → trail SL cross.
    _, update_mock = await _run_variant_to_terminal(
        variant_overrides={
            "tp_pct": Decimal("0.01"),
            "tp_qty_pct": Decimal("0.2"),
            "trail_pct": Decimal("0.003"),
            "sl_pct": Decimal("0.005"),
        },
        candles=[
            _candle_envelope(
                open_=Decimal("65500"),
                high=Decimal("65650"),
                low=Decimal("65500"),
                close=Decimal("65600"),
            ),
            _candle_envelope(
                open_=Decimal("65500"),
                high=Decimal("65500"),
                low=Decimal("65450"),
                close=Decimal("65450"),
                bucket_start=datetime(2026, 5, 8, 12, 2, tzinfo=UTC),
            ),
        ],
    )
    assert update_mock.call_count == 1
    kwargs = update_mock.call_args.kwargs
    assert kwargs["terminal_outcome"] == ShadowVariantTerminal.TP_TRAIL


async def test_variant_runs_to_tp_full_terminal() -> None:
    # Long entry 65000; tp_pct=0.01 → TP=65650; tp_qty_pct=1.0 → Full.
    # Candle 1: high=65650 → full TP cross.
    _, update_mock = await _run_variant_to_terminal(
        variant_overrides={
            "tp_pct": Decimal("0.01"),
            "tp_qty_pct": Decimal("1"),
            "sl_pct": Decimal("0.005"),
        },
        candles=[
            _candle_envelope(
                open_=Decimal("65500"),
                high=Decimal("65650"),
                low=Decimal("65500"),
                close=Decimal("65600"),
            ),
        ],
    )
    assert update_mock.call_count == 1
    kwargs = update_mock.call_args.kwargs
    assert kwargs["terminal_outcome"] == ShadowVariantTerminal.TP_FULL


async def test_variant_runs_to_timeout_terminal() -> None:
    # max_duration_hours = 0 → timeout_seconds = 0 → asyncio.wait_for raises
    # TimeoutError immediately; no candles needed.
    _, update_mock = await _run_variant_to_terminal(
        variant_overrides={
            "max_duration_hours": 0,
            "sl_pct": Decimal("0.005"),
            "tp_pct": Decimal("0.01"),
        },
        candles=[],  # No candles; timeout fires regardless.
    )
    assert update_mock.call_count == 1
    kwargs = update_mock.call_args.kwargs
    assert kwargs["terminal_outcome"] == ShadowVariantTerminal.TIMEOUT
    assert kwargs["realized_pnl"] is None


# ---------------------------------------------------------------------------
# Test 8 — set_trading_stop rejects sl_type='trail' (ADR-0005 v2 H-024 guard)
# ---------------------------------------------------------------------------


async def test_set_trading_stop_rejects_sl_type_trail_kwarg() -> None:
    bus, _, _ = _make_bus_with_log()
    pe = PaperExchange(
        seed_balance=Decimal("10000"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test"),
        bus=bus,
        slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
        pool=_make_pool(),
    )
    with pytest.raises(ValueError, match="sl_type='trail' may only be set by _drain_partial_tp"):
        await pe.set_trading_stop(
            symbol="BTCUSDT",
            tpsl_mode="Full",
            sl_price=Decimal("64000"),
            sl_type="trail",
        )


# ---------------------------------------------------------------------------
# Test 9 — _terminal_from_pe_state truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exec_type", "sl_type", "tpsl_mode", "expected"),
    [
        ("sl", "protective", "Full", ShadowVariantTerminal.SL_HIT),
        ("sl", "be", "Full", ShadowVariantTerminal.BE_HIT),
        ("sl", "trail", "Full", ShadowVariantTerminal.TP_TRAIL),
        ("tp", "protective", "Full", ShadowVariantTerminal.TP_FULL),
        ("tp", "trail", "Partial", ShadowVariantTerminal.TP_FULL),
    ],
)
def test_terminal_from_pe_state_truth_table(
    exec_type: str, sl_type: str, tpsl_mode: str, expected: ShadowVariantTerminal
) -> None:
    result = _terminal_from_pe_state(
        exec_type=exec_type,  # type: ignore[arg-type]
        sl_type_at_close=sl_type,  # type: ignore[arg-type]
        tpsl_mode_at_close=tpsl_mode,  # type: ignore[arg-type]
    )
    assert result == expected


# ---------------------------------------------------------------------------
# Test 10 — H-016 BRIEF §20 verbatim test_shadow_task_unsubscribes_on_exception
# ---------------------------------------------------------------------------


async def test_shadow_task_unsubscribes_on_exception() -> None:
    """BRIEF §20 H-016 verbatim policy "Finalizer unconditional".

    Exception fires AFTER PE + own_sub created (mid-update_shadow_variant_terminal);
    finally block must call pe.bus_unsubscribe_market_ohlc + own_sub.unsubscribe
    + remove task from _active_tasks registry.
    """
    bus, subs_log, sub_mocks = _make_bus_with_log()
    pool = _make_pool()
    worker = _make_worker(bus, pool)
    payload = _make_payload(
        variants=[
            VariantSpec(
                name="t",
                overrides={
                    "max_duration_hours": 0,
                    "sl_pct": Decimal("0.005"),
                    "tp_pct": Decimal("0.01"),
                },
            )
        ],
    )
    insert_mock = AsyncMock(return_value=_make_shadow_variant_row())
    update_mock = AsyncMock(side_effect=RuntimeError("forced fail mid-update"))
    with (
        patch("services.execution.app.shadow_worker.insert_shadow_variant", insert_mock),
        patch("services.execution.app.shadow_worker.update_shadow_variant_terminal", update_mock),
    ):
        await worker._on_shadow_start(payload)
        await asyncio.gather(*worker._active_tasks.get(42, []), return_exceptions=True)

    # Subscriptions registered: [shadow.start.>]? No — worker.start() not called.
    # Only the variant subscriptions fire: PE's market.ohlc.1m.> + own market.ohlc.1m.BTCUSDT.
    pe_sub = next(s for s, _ in subs_log if s == "market.ohlc.1m.>")
    own_sub_subj = next(s for s, _ in subs_log if s == "market.ohlc.1m.BTCUSDT")
    pe_sub_idx = [s for s, _ in subs_log].index(pe_sub)
    own_sub_idx = [s for s, _ in subs_log].index(own_sub_subj)

    # Both subscriptions had unsubscribe called (H-016 finalizer).
    assert sub_mocks[pe_sub_idx].unsubscribe.await_count == 1
    assert sub_mocks[own_sub_idx].unsubscribe.await_count == 1
    # _active_tasks registry cleaned up (parent_trade_id key removed when last task done).
    assert 42 not in worker._active_tasks


# ---------------------------------------------------------------------------
# Bonus: BE-trigger / trail SL pure-helper hand-verification fixtures
# (math-validator Gate 4 cross-checks against plan §hand-verification).
# ---------------------------------------------------------------------------


def test_check_be_trigger_long_at_threshold_fires() -> None:
    # entry=65000, be_trigger=0.005 → cross at 65325 exact.
    assert _check_be_trigger("buy", Decimal("65325"), Decimal("65000"), Decimal("0.005")) is True
    assert (
        _check_be_trigger("buy", Decimal("65324.99"), Decimal("65000"), Decimal("0.005")) is False
    )


def test_compute_be_sl_price_long() -> None:
    # entry=65000, be_sl_level=0.001 → BE-SL = 65065.
    assert _compute_be_sl_price("buy", Decimal("65000"), Decimal("0.001")) == Decimal("65065.000")


def test_compute_trail_sl_price_long() -> None:
    # best=66000, trail_pct=0.003 → trail-SL = 65802.
    assert _compute_trail_sl_price("buy", Decimal("66000"), Decimal("0.003")) == Decimal(
        "65802.000"
    )


# ---------------------------------------------------------------------------
# T-511b2 / ADR-0010 — parent-close H-016 cancellation hook
# ---------------------------------------------------------------------------


async def test_start_subscribes_to_trade_closed_wildcard() -> None:
    """T-511b2: ShadowWorker.start() subscribes to ``trade.closed.>`` (in addition to T-511b1's
    ``shadow.start.>``)."""
    bus, subs_log, _ = _make_bus_with_log()
    pool = MagicMock()
    worker = ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=Decimal("10000"),
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        fee_rate=Decimal("0.0006"),
        clock=lambda: datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    await worker.start()
    subjects = [subject for subject, _handler in subs_log]
    assert "shadow.start.>" in subjects
    assert "trade.closed.>" in subjects


async def test_on_parent_close_cancels_active_tasks_for_trade_id() -> None:
    """T-511b2: _on_parent_close fires .cancel() on every task in _active_tasks[trade_id]."""
    bus, _, _ = _make_bus_with_log()
    pool = MagicMock()
    worker = ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=Decimal("10000"),
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        fee_rate=Decimal("0.0006"),
        clock=lambda: datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    task_a = MagicMock()
    task_a.done = MagicMock(return_value=False)
    task_a.cancel = MagicMock()
    task_b = MagicMock()
    task_b.done = MagicMock(return_value=False)
    task_b.cancel = MagicMock()
    worker._active_tasks[42] = [task_a, task_b]
    payload = TradeClosedPayload(
        parent_trade_id=42,
        parent_kind="live",
        bot_id="alpha",
        closed_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    await worker._on_parent_close(payload)
    task_a.cancel.assert_called_once()
    task_b.cancel.assert_called_once()


async def test_on_parent_close_no_op_when_trade_id_not_in_registry() -> None:
    """T-511b2: _on_parent_close on unknown trade_id → no exception, no cancel attempted."""
    bus, _, _ = _make_bus_with_log()
    pool = MagicMock()
    worker = ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=Decimal("10000"),
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        fee_rate=Decimal("0.0006"),
        clock=lambda: datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    payload = TradeClosedPayload(
        parent_trade_id=999,  # unknown
        parent_kind="paper",
        bot_id="alpha",
        closed_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    # Does NOT raise.
    await worker._on_parent_close(payload)


async def test_on_parent_close_skips_already_done_tasks() -> None:
    """T-511b2: idempotent — cancelled-already tasks are not re-cancelled."""
    bus, _, _ = _make_bus_with_log()
    pool = MagicMock()
    worker = ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=Decimal("10000"),
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        fee_rate=Decimal("0.0006"),
        clock=lambda: datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    task_done = MagicMock()
    task_done.done = MagicMock(return_value=True)
    task_done.cancel = MagicMock()
    worker._active_tasks[42] = [task_done]
    payload = TradeClosedPayload(
        parent_trade_id=42,
        parent_kind="live",
        bot_id="alpha",
        closed_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
    )
    await worker._on_parent_close(payload)
    task_done.cancel.assert_not_called()
