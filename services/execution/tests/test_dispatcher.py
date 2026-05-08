"""§N4 unit tests for :mod:`services.execution.app.dispatcher` (T-218a + T-218b).

Mock-based: adapter (ExchangeClient) + bus (NatsClient) + asyncpg.Pool +
ExecutionEvent constructed inline. Validates DedupingConsumer wrap (H-009),
``run_dispatcher_for_bot`` lifecycle (CancelledError + Exception ERROR + re-raise
per-bot isolation), ``_process`` body (T-218b) covering H-024 v2 derivation,
defensive halt paths (unattributable / orphan_order / orphan_synthetic / over-fill),
INSERT/UPDATE persistence, and close-trigger forward-pointer to T-219.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.bus.dedup import DedupingConsumer
from packages.core import BotId
from packages.db.queries.execution import PositionStateRow, TradeLookupRow
from packages.exchange.types import ExecutionEvent
from services.execution.app import dispatcher as dispatcher_mod
from services.execution.app.dispatcher import (
    ExecutionDispatcher,
    _derive_exec_type,
    run_dispatcher_for_bot,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_FIXED_NOW = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)


def _execution_event(
    *, exchange_exec_id: str = "exec-1", exchange_order_id: str = "ord-1"
) -> ExecutionEvent:
    return ExecutionEvent(
        exchange_exec_id=exchange_exec_id,
        exchange_order_id=exchange_order_id,
        symbol="BTCUSDT",
        side="buy",
        price=Decimal("45000.50"),
        qty=Decimal("0.001"),
        fee=Decimal("0.0001"),
        executed_at=_FIXED_NOW,
    )


def _execution_event_v(
    *,
    exchange_exec_id: str = "exec-1",
    exchange_order_id: str = "ord-1",
    side: str = "sell",
    qty: Decimal = Decimal("5"),
    fee: Decimal = Decimal("0.05"),
    price: Decimal = Decimal("100"),
    symbol: str = "BTCUSDT",
) -> ExecutionEvent:
    """ExecutionEvent factory with full keyword control for T-218b test fixtures."""
    return ExecutionEvent(
        exchange_exec_id=exchange_exec_id,
        exchange_order_id=exchange_order_id,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        price=price,
        qty=qty,
        fee=fee,
        executed_at=_FIXED_NOW,
    )


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
) -> PositionStateRow:
    """PositionStateRow factory."""
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
    )


class _FakeConn:
    """Minimal stand-in for asyncpg.Connection used inside the tx context.

    Real query helpers are patched via monkeypatch; this object is only
    threaded through as the conn argument so signatures align.
    """


@asynccontextmanager
async def _fake_tx() -> AsyncIterator[None]:
    yield None


def _build_pool() -> MagicMock:
    """Pool mock with acquire() → conn ctx + conn.transaction() ctx."""
    conn = _FakeConn()
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[_FakeConn]:
        yield conn

    pool.acquire = _acquire
    # Real asyncpg returns a transaction context-manager from conn.transaction();
    # _FakeConn's `transaction` attr is added dynamically here.
    conn.transaction = lambda: _fake_tx()  # type: ignore[attr-defined]
    return pool


def _build(*, capacity: int = 100) -> tuple[ExecutionDispatcher, MagicMock]:
    pool = _build_pool()
    bus = MagicMock()
    bus.publish = AsyncMock()
    logger = MagicMock()
    adapter = MagicMock()
    adapter.get_closed_pnl_cumulative = AsyncMock(return_value=Decimal("0"))
    dispatcher = ExecutionDispatcher(
        bot_id=BotId("alpha"),
        pool=pool,
        bus=bus,
        bound_logger=logger,
        capacity=capacity,
        now_fn=lambda: _FIXED_NOW,
        adapter=adapter,
        sub_account="alpha-sub",
        closed_pnl_lock=asyncio.Lock(),
        closed_pnl_post_close_sleep_s=0.0,
    )
    return dispatcher, logger


@pytest.fixture
def patched_queries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch all DB query helpers + reconcile_close on dispatcher_mod.

    Returns dict of mocks for assertion. Default behaviors:
    - select_order_id_by_exchange_id → returns None (synthetic)
    - select_trade_by_open_order_id → returns None
    - select_trade_by_close_order_id → returns None
    - select_position_state → returns None (overridden per test)
    - select_open_order_id_by_trade_id → returns 100
    - insert_execution → no-op
    - update_position_state_after_fill → no-op
    - update_trade_fees_incremental → no-op
    - reconcile_close → returns (OrderClosed, correlation_id, exch_order_id) tuple per T-219
    - emit_post_commit_close_event → no-op (caller-side; no actual NATS publish in tests)
    """
    from packages.bus.schemas.orders import OrderClosed
    from packages.core import CorrelationId

    fake_payload = OrderClosed(
        bot_id="alpha",
        order_id=100,
        exchange_order_id="ord-1",
        symbol="BTCUSDT",
        timestamp=_FIXED_NOW,
        realized_pnl=Decimal("0"),
        close_reason="manual",
    )
    mocks: dict[str, Any] = {
        "select_order_id_by_exchange_id": AsyncMock(return_value=None),
        "select_trade_by_open_order_id": AsyncMock(return_value=None),
        "select_trade_by_close_order_id": AsyncMock(return_value=None),
        "select_position_state": AsyncMock(return_value=None),
        "select_open_order_id_by_trade_id": AsyncMock(return_value=100),
        "insert_execution": AsyncMock(return_value=None),
        "update_position_state_after_fill": AsyncMock(return_value=None),
        "update_trade_fees_incremental": AsyncMock(return_value=None),
        "reconcile_close": AsyncMock(
            return_value=(fake_payload, CorrelationId("cid-default"), "ord-1")
        ),
        "emit_post_commit_close_event": AsyncMock(return_value=None),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(dispatcher_mod, name, mock)
    return mocks


# ---------------------------------------------------------------------------
# H-009 dedup ring (verbatim test_duplicate_exec_event_is_ignored)
# ---------------------------------------------------------------------------


async def test_execution_dispatcher_dedup_ring_drops_duplicate_exchange_exec_id() -> None:
    """§20 H-009 verbatim test pin (per `test_duplicate_exec_event_is_ignored` from §20).

    Dedup keyed on ``exchange_exec_id`` — second event with same exec_id is
    dropped silently before _process is invoked.
    """
    dispatcher, _ = _build()
    process_calls: list[ExecutionEvent] = []

    async def _capture(message: ExecutionEvent) -> None:
        process_calls.append(message)

    dispatcher._process = _capture  # type: ignore[method-assign]
    event = _execution_event(exchange_exec_id="exec-dup")
    await dispatcher.consume(event)
    await dispatcher.consume(event)
    assert len(process_calls) == 1


async def test_execution_dispatcher_distinct_exec_ids_pass_to_process_handler() -> None:
    """Two distinct exec_ids → both reach _process."""
    dispatcher, _ = _build()
    process_calls: list[ExecutionEvent] = []

    async def _capture(message: ExecutionEvent) -> None:
        process_calls.append(message)

    dispatcher._process = _capture  # type: ignore[method-assign]
    for exec_id in ("exec-1", "exec-2"):
        await dispatcher.consume(_execution_event(exchange_exec_id=exec_id))
    assert len(process_calls) == 2


async def test_execution_dispatcher_capacity_propagates_from_settings_via_ctor() -> None:
    """Settings.dispatch_dedup_capacity threads to DedupingConsumer base via ctor."""
    dispatcher, _ = _build(capacity=42)
    # Access internal _capacity via base class to verify propagation.
    assert dispatcher._capacity == 42


async def test_execution_dispatcher_subclasses_DedupingConsumer_with_ExecutionEvent_generic() -> (
    None
):
    """Type pin — ExecutionDispatcher IS a DedupingConsumer (mypy + isinstance pass)."""
    dispatcher, _ = _build()
    assert isinstance(dispatcher, DedupingConsumer)


# ---------------------------------------------------------------------------
# _process NotImplementedError forward-pointer to T-218b
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# bot_id public property
# ---------------------------------------------------------------------------


async def test_execution_dispatcher_exposes_public_bot_id_property() -> None:
    """WG#9 fix — public bot_id property (avoids SLF001 in run_dispatcher_for_bot)."""
    dispatcher, _ = _build()
    assert dispatcher.bot_id == BotId("alpha")


# ---------------------------------------------------------------------------
# run_dispatcher_for_bot pump + lifecycle
# ---------------------------------------------------------------------------


def _make_adapter_with_stream(events: list[ExecutionEvent]) -> MagicMock:
    """Build a mock adapter whose stream_executions() yields the given events."""
    adapter = MagicMock()

    async def _stream() -> AsyncIterator[ExecutionEvent]:
        for e in events:
            yield e

    adapter.stream_executions = MagicMock(return_value=_stream())
    return adapter


async def test_run_dispatcher_for_bot_pumps_stream_executions_into_consume() -> None:
    """run_dispatcher_for_bot pumps each yielded event through dispatcher.consume.

    Replace _process with an AsyncMock so consume returns cleanly per event.
    """
    dispatcher, _ = _build()
    consume_calls: list[ExecutionEvent] = []

    async def _capture_process(message: ExecutionEvent) -> None:
        consume_calls.append(message)

    dispatcher._process = _capture_process  # type: ignore[method-assign]

    events = [
        _execution_event(exchange_exec_id="exec-1"),
        _execution_event(exchange_exec_id="exec-2"),
    ]
    adapter = _make_adapter_with_stream(events)
    logger = MagicMock()
    await run_dispatcher_for_bot(adapter=adapter, dispatcher=dispatcher, bound_logger=logger)
    assert len(consume_calls) == 2


async def test_run_dispatcher_for_bot_propagates_cancellederror_without_log_noise() -> None:
    """WG#3 — CancelledError propagated cleanly; no error log emit (graceful shutdown)."""
    dispatcher, _ = _build()

    async def _stream() -> AsyncIterator[ExecutionEvent]:
        # Yield once so consume is reached, then sleep until cancelled.
        yield _execution_event()
        await asyncio.sleep(3600)

    async def _no_op_process(message: ExecutionEvent) -> None:
        return None

    dispatcher._process = _no_op_process  # type: ignore[method-assign]
    adapter = MagicMock()
    adapter.stream_executions = MagicMock(return_value=_stream())
    logger = MagicMock()
    task = asyncio.create_task(
        run_dispatcher_for_bot(adapter=adapter, dispatcher=dispatcher, bound_logger=logger)
    )
    await asyncio.sleep(0)  # let task start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # No ERROR log emit on graceful cancel.
    error_calls = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.dispatcher_stream_terminated" not in error_calls


async def test_run_dispatcher_for_bot_logs_error_and_reraises_on_stream_exception() -> None:
    """Per-bot isolation — log ERROR + re-raise; lifespan gathers with return_exceptions."""
    dispatcher, _ = _build()

    async def _failing_stream() -> AsyncIterator[ExecutionEvent]:
        yield _execution_event()
        raise RuntimeError("ws disconnect mid-flight")

    async def _no_op_process(message: ExecutionEvent) -> None:
        return None

    dispatcher._process = _no_op_process  # type: ignore[method-assign]
    adapter = MagicMock()
    adapter.stream_executions = MagicMock(return_value=_failing_stream())
    logger = MagicMock()
    with pytest.raises(RuntimeError, match="ws disconnect mid-flight"):
        await run_dispatcher_for_bot(adapter=adapter, dispatcher=dispatcher, bound_logger=logger)
    error_calls = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.dispatcher_stream_terminated" in error_calls


# ---------------------------------------------------------------------------
# T-218b — _process body: orders-lookup branches (open / close)
# ---------------------------------------------------------------------------


async def test_process_open_fill_orders_lookup_to_open_branch(
    patched_queries: dict[str, Any],
) -> None:
    """Orders row exists, trade.open_order_id matches → exec_type='open'.

    Per T-218b H-030 fix: open-fill must NOT decrement remaining_qty.
    Realistic placement-time pre-fill ``remaining_qty`` matches event ``qty``
    (placement_persist.py:419 wrote remaining_qty=request.qty); without the
    H-030 skip, dispatcher would zero remaining_qty → trigger close-flow.
    """
    patched_queries["select_order_id_by_exchange_id"].return_value = 100
    patched_queries["select_trade_by_open_order_id"].return_value = TradeLookupRow(
        id=1,
        open_order_id=100,
        close_order_id=None,
        side="buy",
    )
    # Pre-fill remaining_qty matches event.qty (default Decimal("5") per
    # _execution_event_v factory) — placement-time realistic value.
    patched_queries["select_position_state"].return_value = _ps_row(
        remaining_qty=Decimal("5"),
    )
    dispatcher, _ = _build()
    await dispatcher.consume(_execution_event_v(exchange_exec_id="exec-open"))
    insert_call = patched_queries["insert_execution"].call_args
    assert insert_call.kwargs["exec_type"] == "open"
    assert insert_call.kwargs["order_id"] == 100
    assert insert_call.kwargs["trade_id"] == 1
    # H-030 fix invariants: open-fill NEVER decrements remaining_qty + NEVER
    # triggers close-flow. update_trade_fees_incremental still records entry fee.
    patched_queries["update_position_state_after_fill"].assert_not_called()
    patched_queries["reconcile_close"].assert_not_called()
    patched_queries["update_trade_fees_incremental"].assert_called_once()


async def test_process_open_fill_does_not_decrement_remaining_qty(
    patched_queries: dict[str, Any],
) -> None:
    """H-030 regression guard: open fill must NOT decrement remaining_qty.

    Reproduction setup mirrors placement-time pre-fill state:
    placement_persist.py:419 writes ``remaining_qty=request.qty``; the WS
    execution event for the open fill arrives with ``message.qty == request.qty``.
    Pre-T-218b fix, dispatcher subtracted full qty → remaining_qty zeroed →
    reconcile_close triggered → trade marked closed in DB while position open
    on exchange.

    Post-fix: dispatcher skips ``update_position_state_after_fill`` for
    ``exec_type='open'`` (placement-tx already accounted-for the qty);
    ``insert_execution`` still writes audit row; ``update_trade_fees_incremental``
    still records entry fee. Defensive close-trigger guard at dispatcher.py:237
    additionally gates on ``exec_type != 'open'``.
    """
    patched_queries["select_order_id_by_exchange_id"].return_value = 100
    patched_queries["select_trade_by_open_order_id"].return_value = TradeLookupRow(
        id=1,
        open_order_id=100,
        close_order_id=None,
        side="buy",
    )
    # Pre-fill remaining_qty == event.qty (the BUG TRIGGER condition).
    patched_queries["select_position_state"].return_value = _ps_row(
        remaining_qty=Decimal("5"),
    )
    dispatcher, _ = _build()
    await dispatcher.consume(_execution_event_v(exchange_exec_id="exec-open-regression"))
    insert_call = patched_queries["insert_execution"].call_args
    # H-030 invariants:
    assert insert_call.kwargs["exec_type"] == "open"  # exec_type derivation unchanged
    patched_queries["update_position_state_after_fill"].assert_not_called()  # skip per fix
    patched_queries["reconcile_close"].assert_not_called()  # close-trigger gated
    patched_queries["update_trade_fees_incremental"].assert_called_once()  # entry fee recorded


async def test_process_close_fill_orders_lookup_to_close_branch(
    patched_queries: dict[str, Any],
) -> None:
    """Orders row exists, trade.close_order_id matches → exec_type='close'."""
    patched_queries["select_order_id_by_exchange_id"].return_value = 200
    patched_queries["select_trade_by_close_order_id"].return_value = TradeLookupRow(
        id=2,
        open_order_id=100,
        close_order_id=200,
        side="buy",
    )
    patched_queries["select_position_state"].return_value = _ps_row(
        trade_id=2,
        remaining_qty=Decimal("5"),
    )
    dispatcher, _ = _build()
    await dispatcher.consume(_execution_event_v(exchange_exec_id="exec-close"))
    insert_call = patched_queries["insert_execution"].call_args
    assert insert_call.kwargs["exec_type"] == "close"
    assert insert_call.kwargs["order_id"] == 200
    assert insert_call.kwargs["trade_id"] == 2


# ---------------------------------------------------------------------------
# T-218b — _process body: synthetic-fill inference (partial_tp / trail / sl)
# ---------------------------------------------------------------------------


async def test_process_partial_tp_inferred_from_position_state_when_no_orders_row(
    patched_queries: dict[str, Any],
) -> None:
    """Synthetic + opposite + qty<remaining → 'partial_tp' + sl_type='trail' write."""
    # ps pre-fill: side=buy, remaining=10, sl_type='protective'
    patched_queries["select_position_state"].side_effect = [
        _ps_row(remaining_qty=Decimal("10"), sl_type="protective"),  # 1st: derivation
        _ps_row(remaining_qty=Decimal("5"), sl_type="trail"),  # 2nd: ps_after re-read
    ]
    dispatcher, _ = _build()
    event = _execution_event_v(side="sell", qty=Decimal("5"))
    await dispatcher.consume(event)
    insert_call = patched_queries["insert_execution"].call_args
    assert insert_call.kwargs["exec_type"] == "partial_tp"
    assert insert_call.kwargs["order_id"] == 100  # via select_open_order_id_by_trade_id
    update_call = patched_queries["update_position_state_after_fill"].call_args
    assert update_call.kwargs["new_sl_type"] == "trail"
    patched_queries["reconcile_close"].assert_not_called()


async def test_process_trail_inferred_when_remaining_zeroes_with_sl_type_trail(
    patched_queries: dict[str, Any],
) -> None:
    """Synthetic + opposite + qty==remaining + sl_type='trail' → 'trail'."""
    patched_queries["select_position_state"].side_effect = [
        _ps_row(remaining_qty=Decimal("5"), sl_type="trail"),
        _ps_row(remaining_qty=Decimal("0"), sl_type="trail"),  # close trigger
    ]
    dispatcher, _ = _build()
    event = _execution_event_v(side="sell", qty=Decimal("5"))
    await dispatcher.consume(event)
    insert_call = patched_queries["insert_execution"].call_args
    assert insert_call.kwargs["exec_type"] == "trail"
    update_call = patched_queries["update_position_state_after_fill"].call_args
    assert update_call.kwargs["new_sl_type"] is None
    patched_queries["reconcile_close"].assert_called_once()
    patched_queries["emit_post_commit_close_event"].assert_called_once()


async def test_process_sl_inferred_when_remaining_zeroes_with_sl_type_protective(
    patched_queries: dict[str, Any],
) -> None:
    """Synthetic + opposite + qty==remaining + sl_type='protective' → 'sl'."""
    patched_queries["select_position_state"].side_effect = [
        _ps_row(remaining_qty=Decimal("10"), sl_type="protective"),
        _ps_row(remaining_qty=Decimal("0"), sl_type="protective"),
    ]
    dispatcher, _ = _build()
    event = _execution_event_v(side="sell", qty=Decimal("10"))
    await dispatcher.consume(event)
    insert_call = patched_queries["insert_execution"].call_args
    assert insert_call.kwargs["exec_type"] == "sl"


# ---------------------------------------------------------------------------
# T-218b — H-024 v2 binding test (ADR-0005)
# ---------------------------------------------------------------------------


async def test_post_tp_close_fill_labeled_per_db_sl_type_not_exchange_orderlink(
    patched_queries: dict[str, Any],
) -> None:
    """H-024 v2 binding (ADR-0005). Cross-ref brief test_sl_fill_after_partial_tp_labeled_sl_not_tp.

    Per ADR-0005 (v2 semantic): partial_tp promotes sl_type='trail' → next
    opposite-side full-close fill is labeled 'trail' (NOT 'sl' per v1 brief
    name; NOT 'tp' per H-024 hazard regardless of exchange orderLinkId).
    Two-fill sequence per Hand verification table in docs/plans/T-218b.md.
    """
    # Fill 1: partial_tp (qty=5 < remaining=10), promotes sl_type to 'trail'.
    # Fill 2: close fill (qty=5 == remaining=5), reads sl_type='trail' → 'trail'.
    patched_queries["select_position_state"].side_effect = [
        _ps_row(remaining_qty=Decimal("10"), sl_type="protective"),  # Fill 1: derivation
        _ps_row(remaining_qty=Decimal("5"), sl_type="trail"),  # Fill 1: ps_after
        _ps_row(remaining_qty=Decimal("5"), sl_type="trail"),  # Fill 2: derivation
        _ps_row(remaining_qty=Decimal("0"), sl_type="trail"),  # Fill 2: ps_after
    ]
    # Mock reconcile_close to no-op so Fill 2 commits cleanly for assertion-readability
    # (companion test `test_post_tp_close_full_tx_persists_atomically` covers same flow).
    # reconcile_close already returns tuple by default (T-219 contract); keep default.

    dispatcher, _ = _build()
    fill1 = _execution_event_v(
        exchange_exec_id="exec-tp",
        exchange_order_id="SYNTH-LINK",
        side="sell",
        qty=Decimal("5"),
        fee=Decimal("0.05"),
    )
    fill2 = _execution_event_v(
        exchange_exec_id="exec-sl",
        exchange_order_id="SYNTH-LINK",  # SAME orderLinkId
        side="sell",
        qty=Decimal("5"),
        fee=Decimal("0.05"),
    )
    await dispatcher.consume(fill1)
    await dispatcher.consume(fill2)
    insert_calls = patched_queries["insert_execution"].call_args_list
    assert insert_calls[0].kwargs["exec_type"] == "partial_tp"
    # Per ADR-0005: Fill 2 labeled 'trail', NOT 'sl' (brief v1 name) and NOT 'tp'
    # (exchange-link inheritance — H-024 hazard).
    assert insert_calls[1].kwargs["exec_type"] == "trail"


# ---------------------------------------------------------------------------
# T-218b — _process body: defensive halt paths (raise + tx rollback)
# ---------------------------------------------------------------------------


async def test_process_unknown_when_no_position_state_and_no_order_match(
    patched_queries: dict[str, Any],
) -> None:
    """No orders match + no position_state → unattributable → ERROR + RuntimeError."""
    # Both lookups already return None by default fixture.
    dispatcher, logger = _build()
    with pytest.raises(RuntimeError, match="unattributable fill"):
        await dispatcher.consume(_execution_event_v())
    error_event_names = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.dispatcher_unattributable_fill" in error_event_names
    patched_queries["insert_execution"].assert_not_called()


async def test_process_unattributable_fill_raises_runtime_error_and_rolls_back(
    patched_queries: dict[str, Any],
) -> None:
    """B4 — unattributable fill raises before INSERT; no execution row persisted."""
    dispatcher, _ = _build()
    with pytest.raises(RuntimeError, match="unattributable fill"):
        await dispatcher.consume(_execution_event_v())
    patched_queries["insert_execution"].assert_not_called()
    patched_queries["update_position_state_after_fill"].assert_not_called()


async def test_process_orphan_order_match_raises_runtime_error_and_rolls_back(
    patched_queries: dict[str, Any],
) -> None:
    """Pass-3 fix — orders row exists but no trade references it → halt + rollback."""
    patched_queries["select_order_id_by_exchange_id"].return_value = 100
    # both trade lookups return None → orphan order match
    dispatcher, logger = _build()
    with pytest.raises(RuntimeError, match="orphan order match"):
        await dispatcher.consume(_execution_event_v())
    error_event_names = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.dispatcher_orphan_order_halt" in error_event_names
    patched_queries["insert_execution"].assert_not_called()


async def test_process_orphan_synthetic_fill_raises_runtime_error_and_rolls_back(
    patched_queries: dict[str, Any],
) -> None:
    """Approval-pass test pin — synthetic fill resolves trade_id but trades row missing.

    select_position_state returns ps with trade_id=99, but
    select_open_order_id_by_trade_id(99) returns None (race with hypothetical
    T-219 close DELETE). _process raises RuntimeError + tx rollback.
    """
    patched_queries["select_position_state"].return_value = _ps_row(trade_id=99)
    patched_queries["select_open_order_id_by_trade_id"].return_value = None
    dispatcher, logger = _build()
    event = _execution_event_v(side="sell", qty=Decimal("5"))
    with pytest.raises(RuntimeError, match="orphan synthetic fill"):
        await dispatcher.consume(event)
    error_event_names = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.dispatcher_orphan_synthetic_fill" in error_event_names
    patched_queries["insert_execution"].assert_not_called()


async def test_process_overfill_halts_with_runtime_error_preserves_remaining_qty(
    patched_queries: dict[str, Any],
) -> None:
    """B2 fix — event.qty > ps.remaining_qty → ERROR overfill_halt + RuntimeError + rollback.

    Preserves §9.5:1613 invariant `qty_closed + remaining_qty == entry_qty`.
    """
    # Setup: synthetic, opposite-side, qty > remaining.
    patched_queries["select_position_state"].return_value = _ps_row(
        side="buy",
        remaining_qty=Decimal("3"),
        sl_type="protective",
    )
    dispatcher, logger = _build()
    event = _execution_event_v(side="sell", qty=Decimal("10"))  # 10 > 3
    with pytest.raises(RuntimeError, match="over-fill"):
        await dispatcher.consume(event)
    error_event_names = [call.args[0] for call in logger.error.call_args_list]
    assert "execution.dispatcher_overfill_halt" in error_event_names
    patched_queries["insert_execution"].assert_not_called()
    patched_queries["update_position_state_after_fill"].assert_not_called()


# ---------------------------------------------------------------------------
# T-218b — _process body: persistence + close-trigger
# ---------------------------------------------------------------------------


async def test_process_invokes_update_trade_fees_incremental_with_event_fee(
    patched_queries: dict[str, Any],
) -> None:
    """WG#11 — fee threading verified: update_trade_fees_incremental called with event.fee."""
    patched_queries["select_order_id_by_exchange_id"].return_value = 100
    patched_queries["select_trade_by_open_order_id"].return_value = TradeLookupRow(
        id=1,
        open_order_id=100,
        close_order_id=None,
        side="buy",
    )
    patched_queries["select_position_state"].return_value = _ps_row(remaining_qty=Decimal("5"))
    dispatcher, _ = _build()
    event = _execution_event_v(fee=Decimal("0.0123"))
    await dispatcher.consume(event)
    fees_call = patched_queries["update_trade_fees_incremental"].call_args
    assert fees_call.kwargs["fee_delta"] == Decimal("0.0123")
    assert fees_call.kwargs["trade_id"] == 1


async def test_process_open_fill_with_zero_remaining_qty_does_NOT_trigger_close(
    patched_queries: dict[str, Any],
) -> None:
    """T-218b H-030 defensive guard — open-fill must NEVER trigger close-flow.

    REPURPOSED from former B1 test ``test_process_invokes_reconcile_close_when_remaining_zeroes``
    which encoded the BUG behavior as expected (exec_type='open' + remaining_qty=0
    → reconcile_close fires). H-030 fix gates close-trigger on
    ``exec_type != 'open'`` defensively; B1 state-based-trigger invariant is
    preserved for non-open exec_types (covered by companion test
    ``test_process_close_trigger_pure_state_based_independent_of_exec_type``
    using exec_type='unknown').

    Edge case under guard: even if some other path zeroes remaining_qty during
    open-fill processing (state-inconsistency edge case per operator OQ-2
    2026-05-08), close-flow MUST NOT fire because exec_type='open' implies
    placement-tx already committed the position state — re-zeroing is bug
    surface, not legitimate close.
    """
    patched_queries["select_order_id_by_exchange_id"].return_value = 100
    patched_queries["select_trade_by_open_order_id"].return_value = TradeLookupRow(
        id=1,
        open_order_id=100,
        close_order_id=None,
        side="buy",
    )
    # Edge case: ps_after returns remaining_qty=0 during open-fill processing.
    # H-030 defensive guard MUST prevent close-trigger from firing.
    patched_queries["select_position_state"].return_value = _ps_row(
        remaining_qty=Decimal("0"),
    )
    dispatcher, _ = _build()
    await dispatcher.consume(_execution_event_v())
    insert_call = patched_queries["insert_execution"].call_args
    assert insert_call.kwargs["exec_type"] == "open"
    # H-030 invariants: open-fill NEVER decrements remaining_qty AND NEVER
    # triggers close-flow even when defensive ps_after re-read shows zero.
    patched_queries["update_position_state_after_fill"].assert_not_called()
    patched_queries["reconcile_close"].assert_not_called()
    patched_queries["emit_post_commit_close_event"].assert_not_called()
    patched_queries["update_trade_fees_incremental"].assert_called_once()


async def test_process_close_trigger_pure_state_based_independent_of_exec_type(
    patched_queries: dict[str, Any],
) -> None:
    """B1 binding pin — close trigger is `remaining_qty == 0`, NOT gated on exec_type/sl_type.

    Edge case: exec_type='unknown' (sl_type_unrecognized branch) but
    position_state.remaining_qty zeroes post-update → reconcile_close MUST fire.
    """
    # Synthetic + opposite + qty==remaining + sl_type unrecognized → 'unknown' branch.
    # 3 select_position_state calls: derivation, over-fill check (same ps), ps_after.
    patched_queries["select_position_state"].side_effect = [
        _ps_row(remaining_qty=Decimal("5"), sl_type="weird"),  # derivation: unknown
        _ps_row(remaining_qty=Decimal("5"), sl_type="weird"),  # over-fill check: same ps
        _ps_row(remaining_qty=Decimal("0"), sl_type="weird"),  # ps_after: zero → trigger
    ]
    dispatcher, _ = _build()
    event = _execution_event_v(side="sell", qty=Decimal("5"))
    await dispatcher.consume(event)
    insert_call = patched_queries["insert_execution"].call_args
    assert insert_call.kwargs["exec_type"] == "unknown"
    patched_queries["reconcile_close"].assert_called_once()


async def test_post_tp_close_full_tx_persists_atomically(
    patched_queries: dict[str, Any],
) -> None:
    """T-219-survival pin — INSERT + UPDATE atomicity when close-flow succeeds.

    Mocks reconcile_close as no-op (post-T-219 future state). Verifies
    full happy-path commit: insert_execution + update_position_state_after_fill
    + update_trade_fees_incremental + reconcile_close all called.
    """
    patched_queries["select_position_state"].side_effect = [
        _ps_row(remaining_qty=Decimal("5"), sl_type="trail"),
        _ps_row(remaining_qty=Decimal("0"), sl_type="trail"),
    ]
    # reconcile_close already returns tuple by default (T-219 contract); keep default.

    dispatcher, _ = _build()
    event = _execution_event_v(side="sell", qty=Decimal("5"))
    await dispatcher.consume(event)
    patched_queries["insert_execution"].assert_called_once()
    patched_queries["update_position_state_after_fill"].assert_called_once()
    patched_queries["update_trade_fees_incremental"].assert_called_once()
    patched_queries["reconcile_close"].assert_called_once()


# ---------------------------------------------------------------------------
# T-218b — _derive_exec_type isolation tests (helper, no INSERT/UPDATE)
# ---------------------------------------------------------------------------


async def test_derive_exec_type_returns_unknown_when_event_qty_exceeds_remaining(
    patched_queries: dict[str, Any],
) -> None:
    """Helper-isolation test: over-fill returns ('unknown', trade_id, sl_type) + WARN.

    NOT against `_process` body (where over-fill is halted per B2 fix).
    """
    patched_queries["select_position_state"].return_value = _ps_row(
        side="buy",
        remaining_qty=Decimal("3"),
        sl_type="protective",
        trade_id=7,
    )
    logger = MagicMock()
    event = _execution_event_v(side="sell", qty=Decimal("10"))
    result = await _derive_exec_type(
        conn=_FakeConn(),  # type: ignore[arg-type]
        bot_id=BotId("alpha"),
        event=event,
        order_id_match=None,
        bound_logger=logger,
    )
    assert result == ("unknown", 7, "protective")
    warning_event_names = [call.args[0] for call in logger.warning.call_args_list]
    assert "execution.dispatcher_exec_type_unknown" in warning_event_names
