"""In-process idempotency dedup ring for signal-gateway (§9.1 step 4, H-010).

Pure class — 10-second TTL cache of recently-seen ``idempotency_key``s.
Belt-and-suspenders with SIGNALS stream ``duplicate_window=2m`` (T-012):
the in-process ring catches the common case (TV re-send within seconds);
NATS server-side dedup catches replays across a signal-gateway restart
when the in-process ring is gone.

§20 hazard H-010: fan-out-before-dedup. ``signals.raw`` is published by
the T-015b2 handler BEFORE this check; the dedup ring only gates the
``signals.validated`` publish + the ``signals`` DB write. Duplicates
still land in the audit stream.

Data structure: ``dict[str, float]`` mapping key to monotonic timestamp
of the first recording. :meth:`check_and_record` purges stale entries
across the whole dict (O(n) per call, microseconds at F0 scale), then
checks / records the target key atomically under an :class:`asyncio.Lock`.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["DedupRing"]


class DedupRing:
    """Per-key idempotency dedup ring with TTL.

    Single-event-loop safe via :class:`asyncio.Lock`. ``clock`` is
    injected for test determinism (default :func:`time.monotonic`);
    the T-015b1 Hypothesis property test
    (:mod:`tests.test_dedup`) exercises the state machine by
    advancing a fake clock without :func:`asyncio.sleep`.

    Scale: at <1000 signals/day (§3.3) and a 10-second TTL, expected
    resident is <1 entry. Every call performs an O(n) stale sweep so
    the dict stays bounded under adversarial unique-key streams — at
    F0 scale this is microseconds; F2+ reassesses if throughput grows.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def check_and_record(self, key: str) -> bool:
        """Return ``True`` iff ``key`` has not been seen within the TTL.

        On a first-seen key (or a key whose prior entry has expired),
        records the current time and returns ``True``. On a duplicate
        within the TTL, leaves the original timestamp in place and
        returns ``False`` — the caller treats this as §9.1 step 4's
        duplicate branch.
        """
        async with self._lock:
            now = self._clock()
            cutoff = now - self._ttl
            stale = [k for k, t in self._seen.items() if t < cutoff]
            for k in stale:
                del self._seen[k]
            if key in self._seen:
                return False
            self._seen[key] = now
            return True
