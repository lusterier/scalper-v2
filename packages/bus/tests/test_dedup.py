"""Tests for :class:`packages.bus.dedup.DedupingConsumer` (T-210).

Mirror :mod:`services.signal_gateway.tests.test_dedup` structure:
unit tests + Hypothesis property test (§17.1) cross-checking the ring
against a naive reference implementation. The reference proves the
production ring's state-machine invariants under arbitrary
``(seq_index, key)`` event sequences.

H-009 named test ``test_duplicate_exec_event_is_ignored`` lives at
T-218 against a real adapter; T-210 ships the base-class equivalent
``test_duplicate_within_ring_is_dropped``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
import structlog
from hypothesis import given
from hypothesis import strategies as st

from packages.bus import DedupingConsumer


@dataclass(frozen=True, slots=True)
class _Msg:
    """Trivial message payload used in tests."""

    exec_id: str
    seq: int = 0


def _key(m: _Msg) -> str:
    return m.exec_id


class _Recorder(DedupingConsumer[_Msg]):
    """Test subclass that records each ``_process`` invocation."""

    def __init__(self, *, capacity: int = 10_000) -> None:
        super().__init__(key_fn=_key, capacity=capacity)
        self.processed: list[_Msg] = []

    async def _process(self, message: _Msg) -> None:
        self.processed.append(message)


# ----- Unit tests ----------------------------------------------------------


async def test_first_seen_invokes_process() -> None:
    consumer = _Recorder()
    await consumer.consume(_Msg(exec_id="exec-1"))
    assert consumer.processed == [_Msg(exec_id="exec-1")]


async def test_duplicate_within_ring_is_dropped() -> None:
    """H-009 base-class invariant. The named adapter test
    ``test_duplicate_exec_event_is_ignored`` lives at T-218."""
    consumer = _Recorder()
    msg = _Msg(exec_id="exec-1")
    await consumer.consume(msg)
    await consumer.consume(msg)  # duplicate — dropped
    assert consumer.processed == [msg]


async def test_distinct_keys_independent() -> None:
    consumer = _Recorder()
    await consumer.consume(_Msg(exec_id="exec-A"))
    await consumer.consume(_Msg(exec_id="exec-B"))
    assert [m.exec_id for m in consumer.processed] == ["exec-A", "exec-B"]


async def test_overflow_evicts_oldest_fifo() -> None:
    """Capacity-1 ring: after inserting B, A is evicted; re-inserting
    A is treated as fresh."""
    consumer = _Recorder(capacity=1)
    await consumer.consume(_Msg(exec_id="A"))
    await consumer.consume(_Msg(exec_id="B"))  # evicts A
    await consumer.consume(_Msg(exec_id="A"))  # fresh again
    assert [m.exec_id for m in consumer.processed] == ["A", "B", "A"]


async def test_ring_stays_at_capacity_under_overflow() -> None:
    """W#3: ring holds *exactly* ``capacity`` entries in stable state
    after N>capacity inserts (not <= capacity)."""
    consumer = _Recorder(capacity=3)
    for i in range(10):
        await consumer.consume(_Msg(exec_id=f"k{i}"))
    assert len(consumer._seen) == 3


async def test_concurrent_consume_dedups_same_key() -> None:
    """Two concurrent consume() calls with the same key result in
    exactly one _process invocation. Lock serialises the ring."""
    consumer = _Recorder()
    msg = _Msg(exec_id="exec-1")
    await asyncio.gather(consumer.consume(msg), consumer.consume(msg))
    assert consumer.processed == [msg]


async def test_default_capacity_is_10000() -> None:
    """Surface invariant per H-009 verbatim ('ring buffer, size 10k')."""
    consumer = _Recorder()
    assert consumer._capacity == 10_000


async def test_custom_key_fn_extracts_correct_key() -> None:
    """``key_fn`` is fully configurable per H-009 verbatim."""

    class ByLen(DedupingConsumer[_Msg]):
        async def _process(self, message: _Msg) -> None:
            pass

    consumer = ByLen(key_fn=lambda m: str(len(m.exec_id)))
    # Two messages with different exec_ids but same len → second is duplicate.
    await consumer.consume(_Msg(exec_id="abc"))
    await consumer.consume(_Msg(exec_id="xyz"))
    assert list(consumer._seen) == ["3"]


async def test_subclass_must_override_process() -> None:
    """Plain DedupingConsumer raises NotImplementedError on _process."""
    consumer: DedupingConsumer[_Msg] = DedupingConsumer(key_fn=_key)
    with pytest.raises(NotImplementedError):
        await consumer.consume(_Msg(exec_id="exec-1"))


async def test_capacity_zero_raises_value_error() -> None:
    """W#2: capacity=0 rejected at construction."""
    with pytest.raises(ValueError, match="capacity must be > 0"):
        _Recorder(capacity=0)


async def test_capacity_negative_raises_value_error() -> None:
    with pytest.raises(ValueError, match="capacity must be > 0"):
        _Recorder(capacity=-5)


async def test_logger_default_is_bound_logger() -> None:
    """W#7: assert default-logger is a usable BoundLogger after
    construction; do NOT compare instance identity (structlog caches
    by name internally, identity is not guaranteed)."""
    consumer = _Recorder()
    assert consumer._logger is not None
    # structlog's stdlib BoundLogger has the .debug method; smoke-call
    # it to confirm the proxy is callable. No assertion on output —
    # logger config lives at packages.observability.configure().
    consumer._logger.debug("smoke_call_during_test", probe=True)


async def test_explicit_logger_passed_through() -> None:
    """Caller-supplied logger is used as-is (DI override path for T-218)."""
    custom = structlog.stdlib.get_logger("test_dedup_explicit_logger")
    consumer = DedupingConsumer[_Msg](key_fn=_key, logger=custom)
    assert consumer._logger is custom


# ----- Hypothesis property test --------------------------------------------


class _ReferenceRing:
    """Naive-but-correct oracle for :class:`DedupingConsumer` ring state.

    Tracks the last ``capacity`` keys via insertion-ordered dict.
    Replicates the production ring's (insert-then-evict) shape.
    Any divergence between the production ring's ``processed`` list
    and the reference's ``processed`` list means the production ring
    has a state-machine bug, not the reference.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._seen: dict[str, None] = {}
        self.processed: list[str] = []

    def consume(self, key: str) -> None:
        if key in self._seen:
            return
        self._seen[key] = None
        if len(self._seen) > self._capacity:
            # FIFO eviction: pop the first-inserted entry.
            oldest = next(iter(self._seen))
            del self._seen[oldest]
        self.processed.append(key)


@given(
    keys=st.lists(
        # Narrow alphabet → frequent duplicates, exercises dedup state machine.
        st.text(alphabet="abcde", min_size=1, max_size=3),
        min_size=0,
        max_size=50,
    ),
    capacity=st.integers(min_value=1, max_value=20),
)
def test_dedup_consumer_matches_reference_under_arbitrary_event_sequences(
    keys: list[str],
    capacity: int,
) -> None:
    """For any ``(key, ...)`` sequence + capacity, DedupingConsumer's
    process-invocation list equals the reference oracle's."""

    async def _replay_consumer() -> list[str]:
        class _Replay(DedupingConsumer[str]):
            def __init__(self) -> None:
                super().__init__(key_fn=lambda s: s, capacity=capacity)
                self.processed: list[str] = []

            async def _process(self, message: str) -> None:
                self.processed.append(message)

        consumer = _Replay()
        for key in keys:
            await consumer.consume(key)
        return consumer.processed

    consumer_results = asyncio.run(_replay_consumer())

    reference = _ReferenceRing(capacity=capacity)
    for key in keys:
        reference.consume(key)

    assert consumer_results == reference.processed
