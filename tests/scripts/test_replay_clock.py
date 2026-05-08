"""T-507b ReplayClock unit tests (3 named tests covering set/now/monotonic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.core.replay_clock import ReplayClock


def test_replay_clock_initial_value_is_returned_by_now() -> None:
    """Initial datetime is the value returned by `now()` until `set()` advances."""
    initial = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    clock = ReplayClock(initial=initial)
    assert clock.now() == initial


def test_replay_clock_set_advances_when_t_greater_than_current() -> None:
    """`set(t)` advances to t when t > current."""
    initial = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    later = initial + timedelta(minutes=5)
    clock = ReplayClock(initial=initial)
    clock.set(later)
    assert clock.now() == later


def test_replay_clock_set_is_noop_when_t_less_than_current() -> None:
    """`set(t)` is no-op when t <= current (monotonic invariant)."""
    initial = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
    earlier = initial - timedelta(minutes=5)
    clock = ReplayClock(initial=initial)
    clock.set(earlier)
    assert clock.now() == initial  # unchanged
