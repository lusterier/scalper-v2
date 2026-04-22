"""Tests for :class:`services.signal_gateway.app.dedup.DedupRing`.

Includes a Hypothesis property test (§17.1) oracle-ing the ring against
a naive reference implementation over arbitrary ``(time, key)`` event
sequences — H-006 companion: guarantees state-machine correctness under
adversarial inputs that a hand-written unit test wouldn't enumerate.
"""

from __future__ import annotations

import asyncio

from hypothesis import given
from hypothesis import strategies as st

from services.signal_gateway.app.dedup import DedupRing


class _Clock:
    """Mutable-time clock stub for deterministic TTL tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def set(self, t: float) -> None:
        self._now = t


# ----- Unit tests ------------------------------------------------------


async def test_first_seen_is_accepted() -> None:
    clock = _Clock()
    ring = DedupRing(ttl_seconds=10.0, clock=clock)
    assert await ring.check_and_record("k1") is True


async def test_duplicate_within_ttl_rejected() -> None:
    clock = _Clock()
    ring = DedupRing(ttl_seconds=10.0, clock=clock)
    assert await ring.check_and_record("k1") is True
    clock.set(5.0)
    assert await ring.check_and_record("k1") is False


async def test_same_key_after_ttl_accepted() -> None:
    clock = _Clock()
    ring = DedupRing(ttl_seconds=10.0, clock=clock)
    assert await ring.check_and_record("k1") is True  # t=0
    clock.set(11.0)  # past the 10s window
    assert await ring.check_and_record("k1") is True


async def test_distinct_keys_independent() -> None:
    clock = _Clock()
    ring = DedupRing(ttl_seconds=10.0, clock=clock)
    assert await ring.check_and_record("k1") is True
    assert await ring.check_and_record("k2") is True
    clock.set(5.0)
    assert await ring.check_and_record("k1") is False  # dup within TTL
    assert await ring.check_and_record("k2") is False


async def test_boundary_strict_less_than_cutoff() -> None:
    """Eviction uses ``t < cutoff`` (strict); equal-boundary entry stays resident."""
    clock = _Clock()
    ring = DedupRing(ttl_seconds=10.0, clock=clock)
    assert await ring.check_and_record("k1") is True  # t=0
    clock.set(10.0)
    # cutoff = 10 - 10 = 0.0; entry at 0.0 is NOT < 0.0 → resident → duplicate.
    assert await ring.check_and_record("k1") is False
    clock.set(10.0001)
    # cutoff = 0.0001; entry at 0.0 IS < 0.0001 → evicted → accept.
    assert await ring.check_and_record("k1") is True


async def test_sweep_bounds_dict_under_unique_key_stream() -> None:
    """Adversarial unique-key burst is bounded after a clock advance + any call."""
    clock = _Clock()
    ring = DedupRing(ttl_seconds=10.0, clock=clock)
    for i in range(1000):
        clock.set(float(i) * 0.001)
        await ring.check_and_record(f"burst-{i}")
    clock.set(100.0)  # all burst entries now stale
    await ring.check_and_record("trigger")
    # Only the trigger entry remains; the sweep evicted the burst.
    assert len(ring._seen) == 1


# ----- Hypothesis property test ----------------------------------------


class _ReferenceRing:
    """Naive-but-correct oracle for :class:`DedupRing`.

    Rebuilds the dict on every call (O(n)) instead of targeted eviction.
    Used only to cross-check the production ring's state machine; any
    divergence means the production ring's invariants are wrong, not
    the reference.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def check_and_record(self, key: str, now: float) -> bool:
        cutoff = now - self._ttl
        self._seen = {k: t for k, t in self._seen.items() if t >= cutoff}
        if key in self._seen:
            return False
        self._seen[key] = now
        return True


@given(
    events=st.lists(
        st.tuples(
            st.floats(
                min_value=0.0,
                max_value=100.0,
                allow_nan=False,
                allow_infinity=False,
            ),
            # Narrow alphabet + short length → frequent key collisions, which
            # is precisely what the dedup state machine is supposed to handle.
            st.text(alphabet="abc", min_size=1, max_size=3),
        ),
        min_size=0,
        max_size=30,
    ),
    ttl=st.floats(
        min_value=0.1,
        max_value=50.0,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_dedup_ring_matches_reference_under_arbitrary_event_sequences(
    events: list[tuple[float, str]],
    ttl: float,
) -> None:
    """For any ordered ``(time, key)`` sequence, DedupRing behaves as the reference.

    Events are sorted by time before replay — the real system only sees
    monotonically-advancing clock values from :func:`time.monotonic`.
    """
    sorted_events = sorted(events, key=lambda ev: ev[0])

    async def _replay_ring() -> list[bool]:
        clock = _Clock()
        ring = DedupRing(ttl_seconds=ttl, clock=clock)
        outs: list[bool] = []
        for t, key in sorted_events:
            clock.set(t)
            outs.append(await ring.check_and_record(key))
        return outs

    ring_results = asyncio.run(_replay_ring())

    reference = _ReferenceRing(ttl_seconds=ttl)
    ref_results = [reference.check_and_record(key, t) for t, key in sorted_events]

    assert ring_results == ref_results
