"""Symbol-map resolution cache for signal-gateway (§9.1 step 6).

Pure class wrapping :class:`asyncpg.Pool` +
:func:`packages.db.queries.signal_gateway.fetch_symbol_mapping` with a
60-second in-process TTL cache. Hot read path for the T-015b2
``/webhook`` handler: TradingView alerts carry pre-mapped symbols
(``BTCUSDT.P``) and the handler needs the canonical Bybit symbol
(``BTCUSDT``) to persist + publish.

Concurrency: cache reads and writes are serialised under
:class:`asyncio.Lock`, but the DB query itself runs **outside** the
lock so concurrent distinct-key misses do not serialise on PG
round-trip. Two simultaneous misses on the **same** key fire two
independent queries — accepted at F0 scale (§3.3: <1000 signals/day);
miss coalescing is an F1+ optimisation.

Invalidation on admin CRUD is deferred to F4+ when analytics-api ships
the symbol-map edit surface. Until then, the 60-second TTL bounds
post-edit staleness to that floor. Negative results (``None`` from
``symbol_map`` miss) are cached with the same TTL so adversarial
unknown-symbol streams don't hammer PG.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from packages.db.queries.signal_gateway import fetch_symbol_mapping

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

__all__ = ["SymbolMapCache"]


class SymbolMapCache:
    """60-second TTL cache over :func:`fetch_symbol_mapping`.

    ``clock`` is injected for test determinism (default
    :func:`time.monotonic`).
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pool = pool
        self._ttl = ttl_seconds
        self._clock = clock
        # value: (resolved_symbol_or_None, recorded_at_monotonic)
        self._cache: dict[str, tuple[str | None, float]] = {}
        self._lock = asyncio.Lock()

    async def resolve(self, input_symbol: str) -> str | None:
        """Return the canonical symbol for ``input_symbol``, or ``None`` if not mapped.

        Cache hit (within TTL) returns the stored value without touching
        PG. Cache miss (absent or stale) queries
        :func:`fetch_symbol_mapping` outside the lock, then writes the
        result back under the lock — a ``None`` is cached as a negative
        result.
        """
        now = self._clock()
        async with self._lock:
            entry = self._cache.get(input_symbol)
            if entry is not None and (now - entry[1]) < self._ttl:
                return entry[0]

        async with self._pool.acquire() as conn:
            resolved = await fetch_symbol_mapping(conn, input_symbol)

        async with self._lock:
            self._cache[input_symbol] = (resolved, self._clock())
        return resolved
