"""Sliding-window rate limiter for signal-gateway webhook ingress (§9.1 step 3, §16.3).

Pure class — takes a string key (e.g., client IP) and returns whether the
request is within the window. No FastAPI coupling here; middleware
wrapping (Request → IP extraction via ``X-Real-IP`` / ``X-Forwarded-For``
→ :meth:`RateLimiter.check_and_record` call) lives in T-015b2 where the
``Request`` is in scope.

Policy: §16.3 fixes the default at 20 requests per 60 seconds per IP.
§20 hazard H-006 is the v2 address ("v1 signal-gateway with no rate
limit was vulnerable to alert storms"); the property test in T-015b1
:mod:`tests.test_rate_limit` verifies boundary behaviour.

Data structure: per-key :class:`collections.deque` of monotonic
timestamps. On :meth:`check_and_record`:

1. Pop-left stale entries (``< now - window``).
2. If ``len >= limit``, return ``False`` (rejected; no append).
3. Else append ``now`` and return ``True`` (accepted).

Memory: deques are capped at ``limit`` entries; total is O(unique keys
* limit). At <1000 signals/day (§3.3) resident is sub-kilobyte. No
background cleanup for T-015b1; F1+ revisits if metrics show growth
on dormant keys.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["RateLimiter"]


class RateLimiter:
    """Per-key sliding-window limiter.

    Single-event-loop safe via :class:`asyncio.Lock`. Uvicorn runs one
    worker in our Dockerfile (no ``--workers``), so the single-loop
    assumption holds; if that ever changes, a shared store (NATS KV,
    Redis) replaces this class.

    ``clock`` is injected for test determinism — pass :func:`time.monotonic`
    in production (default); pass a closure over a mutable counter
    under unit / Hypothesis tests to advance time without
    :func:`asyncio.sleep`.
    """

    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        limit: int = 20,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._limit = limit
        self._clock = clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check_and_record(self, key: str) -> bool:
        """Return ``True`` if ``key`` is within the limit (and record); ``False`` if over.

        Stale entries (older than the window) are popped from the front
        of the per-key deque on every call. An empty deque after eviction
        is left in the dict — small allocation, no practical leak at
        F0 scale.
        """
        async with self._lock:
            now = self._clock()
            window_start = now - self._window
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = deque()
                self._buckets[key] = bucket
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(now)
            return True
