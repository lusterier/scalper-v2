"""Unit tests for :class:`services.alerting.app.dedup.DedupTracker` (T-409, 4 tests)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.alerting.app.dedup import DedupTracker

_T0 = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


def test_is_duplicate_false_on_fresh_key() -> None:
    """First call for a key returns False (not yet marked)."""
    tracker = DedupTracker(window_seconds=300)
    assert tracker.is_duplicate("alpha", now=_T0) is False


def test_is_duplicate_true_within_window() -> None:
    """mark + check within window → True."""
    tracker = DedupTracker(window_seconds=300)
    tracker.mark("alpha", now=_T0)
    assert tracker.is_duplicate("alpha", now=_T0 + timedelta(seconds=100)) is True


def test_is_duplicate_false_after_window() -> None:
    """mark + advance time past window → False (cleanup drops entry)."""
    tracker = DedupTracker(window_seconds=300)
    tracker.mark("alpha", now=_T0)
    assert tracker.is_duplicate("alpha", now=_T0 + timedelta(seconds=301)) is False


def test_cleanup_drops_old_entries_keeps_recent() -> None:
    """Mark 3 keys at staggered times; advance past window; assert old gone, recent kept."""
    tracker = DedupTracker(window_seconds=300)
    tracker.mark("old", now=_T0)
    tracker.mark("middle", now=_T0 + timedelta(seconds=200))
    tracker.mark("recent", now=_T0 + timedelta(seconds=400))

    # Advance to T0+410s — `old` (>300s ago) is dropped;
    # `middle` (210s ago) + `recent` (10s ago) are kept.
    now = _T0 + timedelta(seconds=410)
    assert tracker.is_duplicate("old", now=now) is False
    assert tracker.is_duplicate("middle", now=now) is True
    assert tracker.is_duplicate("recent", now=now) is True
