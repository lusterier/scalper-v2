"""Tests for :class:`services.signal_gateway.app.rate_limit.RateLimiter`."""

from __future__ import annotations

from services.signal_gateway.app.rate_limit import RateLimiter


class _Clock:
    """Mutable-time clock stub for deterministic window tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def set(self, t: float) -> None:
        self._now = t


async def test_first_call_accepted() -> None:
    clock = _Clock()
    rl = RateLimiter(window_seconds=60.0, limit=20, clock=clock)
    assert await rl.check_and_record("ip1") is True


async def test_up_to_limit_accepted() -> None:
    clock = _Clock()
    rl = RateLimiter(window_seconds=60.0, limit=20, clock=clock)
    for i in range(20):
        clock.set(float(i))
        assert await rl.check_and_record("ip1") is True


async def test_over_limit_rejected() -> None:
    clock = _Clock()
    rl = RateLimiter(window_seconds=60.0, limit=3, clock=clock)
    assert await rl.check_and_record("ip1") is True
    clock.set(1.0)
    assert await rl.check_and_record("ip1") is True
    clock.set(2.0)
    assert await rl.check_and_record("ip1") is True
    clock.set(2.5)
    assert await rl.check_and_record("ip1") is False


async def test_accepts_after_eviction() -> None:
    """First entry ages out of the window → capacity frees."""
    clock = _Clock()
    rl = RateLimiter(window_seconds=10.0, limit=2, clock=clock)
    assert await rl.check_and_record("ip1") is True  # t=0
    clock.set(5.0)
    assert await rl.check_and_record("ip1") is True  # t=5
    clock.set(6.0)
    assert await rl.check_and_record("ip1") is False  # at limit
    clock.set(11.0)  # entry at t=0 now stale (0 < 11 - 10)
    assert await rl.check_and_record("ip1") is True


async def test_distinct_keys_independent() -> None:
    clock = _Clock()
    rl = RateLimiter(window_seconds=60.0, limit=2, clock=clock)
    assert await rl.check_and_record("ip1") is True
    clock.set(1.0)
    assert await rl.check_and_record("ip1") is True
    clock.set(2.0)
    assert await rl.check_and_record("ip1") is False  # ip1 at limit
    assert await rl.check_and_record("ip2") is True  # ip2 untouched


async def test_boundary_strict_less_than_eviction() -> None:
    """Eviction uses ``entry < now - window`` (strict); equal-boundary entry stays."""
    clock = _Clock()
    rl = RateLimiter(window_seconds=10.0, limit=1, clock=clock)
    assert await rl.check_and_record("ip1") is True  # t=0 recorded
    clock.set(10.0)
    # now - window = 0.0; entry at 0.0 is NOT strictly < 0.0 → still resident → reject.
    assert await rl.check_and_record("ip1") is False
    clock.set(10.0001)
    # now - window = 0.0001; entry at 0.0 IS < 0.0001 → evicted → accept.
    assert await rl.check_and_record("ip1") is True


async def test_limit_of_zero_rejects_everything() -> None:
    """Degenerate configuration: limit=0 means no requests ever pass."""
    clock = _Clock()
    rl = RateLimiter(window_seconds=60.0, limit=0, clock=clock)
    assert await rl.check_and_record("ip1") is False
