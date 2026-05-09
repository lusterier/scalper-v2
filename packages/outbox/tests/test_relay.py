"""§N4 unit tests for :mod:`packages.outbox.relay` (T-537a2).

Mock-based: ``pool`` + ``bus`` + ``bound_logger`` are AsyncMock / MagicMock
fixtures. SQL semantics (FOR UPDATE SKIP LOCKED, backoff window) are pinned
at testcontainer level by T-537a1 ``tests/integration/queries/test_outbox.py``;
this file focuses on worker orchestration: serial-publish loop, per-event
mark on success/failure, max-attempts exhaustion, sleep on empty batch,
graceful stop, cancellation semantics, logger key constants.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from packages.outbox import relay
from packages.outbox.relay import (
    LOG_EXHAUSTED,
    LOG_POLL_STARTED,
    LOG_PUBLISH_FAILED,
    LOG_PUBLISH_SUCCEEDED,
    LOG_STOPPED,
    OutboxRelayWorker,
)
from packages.outbox.types import OutboxEvent, OutboxRelaySettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import pytest

_FIXED_NOW = datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC)


def _event(
    *,
    event_id: int = 1,
    service: str = "signal-gateway",
    subject: str = "signals.validated",
    correlation_id: str | None = "cid-1",
    payload: dict[str, Any] | None = None,
    attempt_count: int = 0,
) -> OutboxEvent:
    return OutboxEvent(
        id=event_id,
        service=service,
        subject=subject,
        correlation_id=correlation_id,
        payload=payload if payload is not None else {"k": "v"},
        created_at=_FIXED_NOW,
        published_at=None,
        attempt_count=attempt_count,
        last_attempt_at=None,
        last_error=None,
        failed_at=None,
    )


def _build_pool_with_events(events: list[OutboxEvent]) -> tuple[MagicMock, MagicMock]:
    """Pool mock returning ``events`` from select_pending then [] forever.

    Returns ``(pool, conn)`` so tests can inspect mark_* calls on conn.
    """
    pool = MagicMock()
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetch = AsyncMock(side_effect=[_pack_rows(events), []] * 100)

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[MagicMock]:
        yield conn

    pool.acquire = _acquire

    @asynccontextmanager
    async def _tx() -> AsyncIterator[None]:
        yield None

    conn.transaction = lambda: _tx()
    return pool, conn


def _pack_rows(events: list[OutboxEvent]) -> list[dict[str, Any]]:
    """Pack OutboxEvent dataclasses into asyncpg-Row-shaped dicts.

    select_pending_outbox_events SELECT projects 11 columns; mock returns
    dict rows that mimic asyncpg.Record for the dataclass projection.
    """
    return [
        {
            "id": e.id,
            "service": e.service,
            "subject": e.subject,
            "correlation_id": e.correlation_id,
            "payload": e.payload,  # codec-registered path returns dict directly
            "created_at": e.created_at,
            "published_at": e.published_at,
            "attempt_count": e.attempt_count,
            "last_attempt_at": e.last_attempt_at,
            "last_error": e.last_error,
            "failed_at": e.failed_at,
        }
        for e in events
    ]


def _build_worker(
    *,
    pool: MagicMock,
    bus: MagicMock,
    service: str = "signal-gateway",
    settings: OutboxRelaySettings | None = None,
    clock: Any = None,
) -> tuple[OutboxRelayWorker, MagicMock]:
    logger = MagicMock()
    used_settings = settings or OutboxRelaySettings()
    used_clock = clock if clock is not None else (lambda: _FIXED_NOW)
    worker = OutboxRelayWorker(
        pool=pool,
        bus=bus,
        service=service,
        settings=used_settings,
        bound_logger=logger,
        clock=used_clock,
    )
    return worker, logger


# ---------------------------------------------------------------------------
# Test #1 — happy path with envelope construction round-trip
# ---------------------------------------------------------------------------


async def test_run_one_batch_publishes_events_and_marks_published_with_envelope_construction() -> (
    None
):
    """T-537a2 / WG#2 — happy path; envelope constructed from outbox row fields.

    Per BLOCKER #2 fix: payload column stores BUSINESS event dict (not
    serialised envelope); correlation_id is separate column; publisher =
    service. Test verifies envelope construction + bus.publish call args
    + mark_published call args.
    """
    payload_a = {
        "correlation_id_inner": str(uuid4()),
        "ts_inner": _FIXED_NOW.isoformat(),
        "score_inner": str(Decimal("0.42")),
        "symbol": "BTCUSDT",
    }
    payload_b = {"action": "buy", "symbol": "ETHUSDT"}
    events = [
        _event(event_id=1, correlation_id="cid-a", subject="signals.validated", payload=payload_a),
        _event(event_id=2, correlation_id="cid-b", subject="signals.validated", payload=payload_b),
    ]
    pool, conn = _build_pool_with_events(events)
    bus = MagicMock()
    bus.publish = AsyncMock()
    worker, logger = _build_worker(pool=pool, bus=bus, service="signal-gateway")

    processed = await worker._run_one_batch()

    assert processed == 2
    assert bus.publish.await_count == 2
    # First call: subject + envelope.
    call_a = bus.publish.await_args_list[0]
    assert call_a.args[0] == "signals.validated"
    envelope_a = call_a.args[1]
    assert envelope_a.correlation_id == "cid-a"
    assert envelope_a.publisher == "signal-gateway"
    assert envelope_a.payload == payload_a  # dict equality on round-tripped business payload
    # Second call.
    call_b = bus.publish.await_args_list[1]
    assert call_b.args[0] == "signals.validated"
    envelope_b = call_b.args[1]
    assert envelope_b.correlation_id == "cid-b"
    assert envelope_b.publisher == "signal-gateway"
    assert envelope_b.payload == payload_b

    # mark_published called twice; mark_failed never.
    update_calls = [c for c in conn.execute.await_args_list if "UPDATE outbox_events" in c.args[0]]
    published_calls = [c for c in update_calls if "SET published_at" in c.args[0]]
    failed_calls = [c for c in update_calls if "attempt_count + 1" in c.args[0]]
    assert len(published_calls) == 2
    assert len(failed_calls) == 0

    # Logger keys.
    log_keys = [c.args[0] for c in logger.info.call_args_list]
    assert log_keys.count(LOG_POLL_STARTED) == 1
    assert log_keys.count(LOG_PUBLISH_SUCCEEDED) == 2


# ---------------------------------------------------------------------------
# Test #2 — single event publish failure → mark_failed
# ---------------------------------------------------------------------------


async def test_run_one_batch_marks_failed_when_publish_raises() -> None:
    events = [_event(event_id=42, attempt_count=3)]
    pool, conn = _build_pool_with_events(events)
    bus = MagicMock()
    bus.publish = AsyncMock(side_effect=RuntimeError("nats unreachable"))
    worker, logger = _build_worker(pool=pool, bus=bus)

    processed = await worker._run_one_batch()

    assert processed == 1
    assert bus.publish.await_count == 1
    update_calls = [c for c in conn.execute.await_args_list if "UPDATE outbox_events" in c.args[0]]
    failed_calls = [c for c in update_calls if "attempt_count + 1" in c.args[0]]
    assert len(failed_calls) == 1
    failed_args = failed_calls[0].args
    assert failed_args[1] == 42  # event_id
    assert failed_args[3] == "nats unreachable"  # last_error
    error_keys = [c.args[0] for c in logger.error.call_args_list]
    assert LOG_PUBLISH_FAILED in error_keys


# ---------------------------------------------------------------------------
# Test #3 — partial-batch failure isolation
# ---------------------------------------------------------------------------


async def test_partial_batch_failure_does_not_block_peers() -> None:
    events = [
        _event(event_id=1),
        _event(event_id=2),
        _event(event_id=3),
    ]
    pool, conn = _build_pool_with_events(events)
    bus = MagicMock()
    # Middle event fails; first + third succeed.
    bus.publish = AsyncMock(side_effect=[None, RuntimeError("transient"), None])
    worker, _ = _build_worker(pool=pool, bus=bus)

    processed = await worker._run_one_batch()

    assert processed == 3
    assert bus.publish.await_count == 3
    update_calls = [c for c in conn.execute.await_args_list if "UPDATE outbox_events" in c.args[0]]
    published = [c for c in update_calls if "SET published_at" in c.args[0]]
    failed = [c for c in update_calls if "attempt_count + 1" in c.args[0]]
    assert len(published) == 2  # events 1 + 3
    assert len(failed) == 1  # event 2
    assert failed[0].args[1] == 2


# ---------------------------------------------------------------------------
# Test #4 — max-attempts exhaustion logs LOG_EXHAUSTED
# ---------------------------------------------------------------------------


async def test_max_attempts_exhaustion_logs_exhausted_in_addition_to_failed() -> None:
    settings = OutboxRelaySettings(max_attempts=5)
    # Event already at attempt_count=4; this failure makes 4+1=5 → exhausted.
    events = [_event(event_id=99, attempt_count=4)]
    pool, _ = _build_pool_with_events(events)
    bus = MagicMock()
    bus.publish = AsyncMock(side_effect=RuntimeError("boom"))
    worker, logger = _build_worker(pool=pool, bus=bus, settings=settings)

    await worker._run_one_batch()

    error_keys = [c.args[0] for c in logger.error.call_args_list]
    assert LOG_PUBLISH_FAILED in error_keys
    assert LOG_EXHAUSTED in error_keys


async def test_max_attempts_not_yet_exhausted_logs_failed_only() -> None:
    settings = OutboxRelaySettings(max_attempts=5)
    events = [_event(event_id=99, attempt_count=2)]  # 2+1=3, not exhausted.
    pool, _ = _build_pool_with_events(events)
    bus = MagicMock()
    bus.publish = AsyncMock(side_effect=RuntimeError("boom"))
    worker, logger = _build_worker(pool=pool, bus=bus, settings=settings)

    await worker._run_one_batch()

    error_keys = [c.args[0] for c in logger.error.call_args_list]
    assert LOG_PUBLISH_FAILED in error_keys
    assert LOG_EXHAUSTED not in error_keys


# ---------------------------------------------------------------------------
# Test #5 — empty-batch sleep
# ---------------------------------------------------------------------------


async def test_run_loop_sleeps_poll_interval_when_batch_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty batch → asyncio.sleep(poll_interval_s); spy captures the value."""
    pool, _ = _build_pool_with_events([])
    bus = MagicMock()
    bus.publish = AsyncMock()
    settings = OutboxRelaySettings(poll_interval_s=0.42)
    worker, _ = _build_worker(pool=pool, bus=bus, settings=settings)

    sleep_calls: list[float] = []
    sleep_done = asyncio.Event()

    async def _spy_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            sleep_done.set()
        # Yield back to event loop so cancellation can propagate.
        await asyncio.sleep(0) if False else None

    monkeypatch.setattr("packages.outbox.relay.asyncio.sleep", _spy_sleep)

    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(sleep_done.wait(), timeout=2.0)
    await worker.stop()

    assert sleep_calls[0] == 0.42
    assert sleep_calls[1] == 0.42
    assert task.done()


# ---------------------------------------------------------------------------
# Test #6 — stop() during sleep emits stopped log
# ---------------------------------------------------------------------------


async def test_stop_during_sleep_emits_stopped_log() -> None:
    pool, _ = _build_pool_with_events([])
    bus = MagicMock()
    bus.publish = AsyncMock()
    settings = OutboxRelaySettings(poll_interval_s=0.05)
    worker, logger = _build_worker(pool=pool, bus=bus, settings=settings)

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.02)  # let one poll iteration start
    await worker.stop()
    # task is cancelled inside stop(); confirm clean shutdown.
    assert task.done()

    info_keys = [c.args[0] for c in logger.info.call_args_list]
    assert LOG_STOPPED in info_keys


# ---------------------------------------------------------------------------
# Test #7 — cancellation during in-flight publish → silent cancel; no mark_failed
# ---------------------------------------------------------------------------


async def test_cancellation_during_in_flight_publish_does_not_mark_failed() -> None:
    """OQ-2 silent cancel + AC#7 — CancelledError propagates UP uncaught.

    bus.publish is a slow AsyncMock that awaits an asyncio.Event; we cancel
    via worker.stop() while publish is in-flight. The except clause is
    `except Exception` (NOT BaseException) → CancelledError bypasses + tx
    rollbacks → no mark_failed write.
    """
    events = [_event(event_id=77)]
    pool, conn = _build_pool_with_events(events)
    bus = MagicMock()
    publish_in_progress = asyncio.Event()
    release_publish = asyncio.Event()

    async def _slow_publish(_subject: str, _envelope: object) -> None:
        publish_in_progress.set()
        await release_publish.wait()  # park indefinitely

    bus.publish = AsyncMock(side_effect=_slow_publish)
    worker, _ = _build_worker(pool=pool, bus=bus)

    task = asyncio.create_task(worker.run())
    await publish_in_progress.wait()  # publish is mid-flight
    await worker.stop()  # cancel the task

    # No mark_failed write happened (transaction rolled back; even if mark_*
    # had been written, the rollback unwinds them).
    update_calls = [c for c in conn.execute.await_args_list if "UPDATE outbox_events" in c.args[0]]
    failed_calls = [c for c in update_calls if "attempt_count + 1" in c.args[0]]
    assert len(failed_calls) == 0
    assert task.done()


# ---------------------------------------------------------------------------
# Test #8 — logger key constants verbatim + frozenset registry
# ---------------------------------------------------------------------------


def test_logger_key_constants_have_verbatim_values() -> None:
    """T-537a2 / WG#3 — module-level Final constants pinned verbatim."""
    assert relay.LOG_POLL_STARTED == "outbox.relay.poll_started"
    assert relay.LOG_PUBLISH_SUCCEEDED == "outbox.relay.publish_succeeded"
    assert relay.LOG_PUBLISH_FAILED == "outbox.relay.publish_failed"
    assert relay.LOG_EXHAUSTED == "outbox.relay.exhausted"
    assert relay.LOG_STOPPED == "outbox.relay.stopped"


def test_logger_key_frozenset_registry_contains_5_keys() -> None:
    """T-537a2 / WG#3 — _LOG_KEYS frozenset registry pin."""
    assert (
        frozenset(
            {
                "outbox.relay.poll_started",
                "outbox.relay.publish_succeeded",
                "outbox.relay.publish_failed",
                "outbox.relay.exhausted",
                "outbox.relay.stopped",
            }
        )
        == relay._LOG_KEYS
    )


# ---------------------------------------------------------------------------
# Test #9 — stop-before-run idempotency
# ---------------------------------------------------------------------------


async def test_stop_before_run_is_idempotent_and_emits_stopped_log() -> None:
    """Lifespan-startup-failure path: stop() can be called before run() ever ran."""
    pool = MagicMock()
    bus = MagicMock()
    worker, logger = _build_worker(pool=pool, bus=bus)

    # First stop without ever calling run().
    await worker.stop()
    info_keys = [c.args[0] for c in logger.info.call_args_list]
    assert info_keys.count(LOG_STOPPED) == 1

    # Second stop is a no-op (idempotent) — no second LOG_STOPPED emitted.
    await worker.stop()
    info_keys = [c.args[0] for c in logger.info.call_args_list]
    assert info_keys.count(LOG_STOPPED) == 1
