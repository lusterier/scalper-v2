"""§N4 unit tests for :mod:`services.execution.app.dispatcher` (T-218a).

Mock-based: adapter (ExchangeClient) + bus (NatsClient) + asyncpg.Pool +
ExecutionEvent constructed inline. Validates DedupingConsumer wrap (H-009),
NotImplementedError forward-pointer to T-218b body, and run_dispatcher_for_bot
lifecycle (CancelledError path + Exception ERROR + re-raise per-bot isolation).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.bus.dedup import DedupingConsumer
from packages.core import BotId
from packages.exchange.types import ExecutionEvent
from services.execution.app.dispatcher import (
    ExecutionDispatcher,
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


def _build(*, capacity: int = 100) -> tuple[ExecutionDispatcher, MagicMock]:
    pool = MagicMock()
    bus = MagicMock()
    bus.publish = AsyncMock()
    logger = MagicMock()
    dispatcher = ExecutionDispatcher(
        bot_id=BotId("alpha"),
        pool=pool,
        bus=bus,
        bound_logger=logger,
        capacity=capacity,
        now_fn=lambda: _FIXED_NOW,
    )
    return dispatcher, logger


# ---------------------------------------------------------------------------
# H-009 dedup ring (verbatim test_duplicate_exec_event_is_ignored)
# ---------------------------------------------------------------------------


async def test_execution_dispatcher_dedup_ring_drops_duplicate_exchange_exec_id() -> None:
    """§20 H-009 verbatim test pin (per `test_duplicate_exec_event_is_ignored` from §20).

    Dedup keyed on ``exchange_exec_id`` — second event with same exec_id is
    dropped silently before _process is invoked.
    """
    dispatcher, _ = _build()
    event = _execution_event(exchange_exec_id="exec-dup")
    # First call: _process raises NotImplementedError (T-218b stub).
    with pytest.raises(NotImplementedError):
        await dispatcher.consume(event)
    # Second call (duplicate): dedup ring drops; _process NOT invoked → no raise.
    await dispatcher.consume(event)


async def test_execution_dispatcher_distinct_exec_ids_pass_to_process_handler() -> None:
    """Two distinct exec_ids → both reach _process (each raising NotImplementedError)."""
    dispatcher, _ = _build()
    raised_count = 0
    for exec_id in ("exec-1", "exec-2"):
        try:
            await dispatcher.consume(_execution_event(exchange_exec_id=exec_id))
        except NotImplementedError:
            raised_count += 1
    assert raised_count == 2


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


async def test_execution_dispatcher_process_raises_NotImplementedError_with_T_218b_substring() -> (
    None
):
    """WG#2 — _process raises NotImplementedError; message MUST contain 'T-218b' substring."""
    dispatcher, _ = _build()
    with pytest.raises(NotImplementedError) as info:
        await dispatcher.consume(_execution_event())
    assert "T-218b" in str(info.value)


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
