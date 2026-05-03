"""In-process cache for CPU-heavy analytics endpoints (T-406, BRIEF §9.6:1641).

Mirrors execution-service ``closed_pnl_locks: dict[str, asyncio.Lock]``
per-key dict pattern (ADR-0006 D4). Lock-per-key prevents thundering
herd: concurrent identical MC requests share a single compute pass
rather than N parallel computes.

F4 MVP scope: in-process, single-tenant. F5+ revisit if analytics-api
is sharded across processes (NATS KV / Redis distributed cache).

WG#1 (lock granularity): ``_global_lock`` is held ONLY for ``_locks``
dict mutation (`setdefault` for new keys). NEVER held during
``compute_fn()`` callback — that would serialize ALL endpoints behind
one global lock and eliminate per-key benefit.

WG#8 (monotonic growth): ``_locks`` grows monotonically (no eviction).
Per unique cache_key (~3-5 unique aggregate request shapes per
dashboard session) memory growth is negligible. F5+ revisit if
``len(_locks) > 1000`` in production.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

__all__ = ["AnalyticsCache", "cache_key"]


class AnalyticsCache:
    """In-process cache with per-key asyncio.Lock anti-thundering-herd protection.

    F4 MVP: ``_locks`` grows monotonically (no eviction). Per unique
    cache_key (~3-5 unique aggregate request shapes per dashboard
    session) memory growth is negligible. F5+ revisit if
    ``len(_locks) > 1000`` in production.
    """

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}  # key → (expiry_unix_ts, value)
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()  # protects _locks dict mutation only

    async def _get_lock(self, key: str) -> asyncio.Lock:
        """Return per-key lock; create under _global_lock if missing.

        WG#1 — _global_lock held ONLY for setdefault, never longer.
        """
        async with self._global_lock:
            return self._locks.setdefault(key, asyncio.Lock())

    async def get_or_compute(
        self,
        key: str,
        ttl_seconds: int,
        compute_fn: Callable[[], Awaitable[Any]],
        *,
        now_fn: Callable[[], datetime],
    ) -> Any:
        """Return cached value if fresh; else compute under per-key lock + cache.

        Per-key lock prevents thundering herd. Expired entries are
        recomputed lazily on next access. No background eviction (F5+
        if memory pressure surfaces).
        """
        # Fast-path: check cache without lock (race-tolerant: stale read
        # at worst causes redundant compute, never wrong result).
        now_ts = now_fn().timestamp()
        cached = self._values.get(key)
        if cached is not None and cached[0] > now_ts:
            return cached[1]

        # Slow-path: acquire per-key lock for compute + cache update.
        per_key_lock = await self._get_lock(key)
        async with per_key_lock:
            # Re-check inside lock — another caller may have just computed.
            now_ts = now_fn().timestamp()
            cached = self._values.get(key)
            if cached is not None and cached[0] > now_ts:
                return cached[1]
            value = await compute_fn()
            self._values[key] = (now_ts + ttl_seconds, value)
            return value


def cache_key(endpoint: str, params: dict[str, Any]) -> str:
    """SHA256 over endpoint name + sorted params; deterministic same-request hash.

    Sort by key for order-independence: ``cache_key("ep", {"a": 1, "b": 2})``
    equals ``cache_key("ep", {"b": 2, "a": 1})``. Used by Monte-Carlo
    endpoint to share compute across identical request shapes.
    """
    sorted_pairs = sorted((k, str(v)) for k, v in params.items())
    raw = endpoint + "|" + "|".join(f"{k}={v}" for k, v in sorted_pairs)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
