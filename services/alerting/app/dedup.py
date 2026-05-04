"""In-memory dedup tracker with lazy cleanup-on-access (T-409, OQ-4=A).

Per OQ-4=A — F4 single-process scope; restart loses state (acceptable
operational trade-off vs NATS KV bucket complexity). Cleanup happens on
every is_duplicate() call: entries older than `window_seconds` are
removed. O(n) per call where n = active dedup keys; bounded by single-
operator scale (~10-100s of distinct alerts per window).

Per Edge case #14 — if alerts STOP arriving, last-window-state is held
indefinitely until next is_duplicate triggers cleanup. Worst-case 64KB
held indefinitely after burst-then-quiet — bounded, not a leak.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["DedupTracker"]


class DedupTracker:
    """Per-key timestamp map; lazy cleanup on is_duplicate access."""

    def __init__(self, *, window_seconds: int) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._marks: dict[str, datetime] = {}

    def is_duplicate(self, key: str, *, now: datetime) -> bool:
        """Return True if `key` was marked within the dedup window."""
        self._cleanup(now)
        return key in self._marks

    def mark(self, key: str, *, now: datetime) -> None:
        """Record `key` as seen at `now`. Subsequent is_duplicate within
        the window returns True until cleanup drops the entry."""
        self._marks[key] = now

    def _cleanup(self, now: datetime) -> None:
        """Drop entries older than `window_seconds`. Idempotent."""
        cutoff = now - self._window
        # List materialization avoids RuntimeError: dict mutated during iter.
        stale = [k for k, ts in self._marks.items() if ts < cutoff]
        for k in stale:
            del self._marks[k]
