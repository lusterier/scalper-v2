"""§20 H-009 + §9.5 line 1591 DedupingConsumer base class.

Generic, in-memory, size-bounded FIFO ring keyed by configurable
extractor. Subclass overrides :meth:`DedupingConsumer._process` and
binds ``key_fn`` at construction time.

NOT a JetStream-side dedup mechanism (publisher-side
``Nats-Msg-Id`` / ``duplicate_window`` is server-managed; this is
consumer-side, in-process). NOT the signal-gateway DedupRing
(:class:`services.signal_gateway.app.dedup.DedupRing` is TTL-based;
this is size-bounded). Both layers can coexist; they address
different replay sources.

Two layers, distinct contracts:

============================  =====================  ====================
Layer                         Where                  Scope
============================  =====================  ====================
JetStream duplicate_window    NATS server            cross-publisher idempotency window
DedupingConsumer (this file)  consumer process       in-process WS-replay drop
============================  =====================  ====================

Single event loop assumption: the internal :class:`asyncio.Lock`
serialises concurrent ``consume`` invocations from the same loop.
Cross-process / cross-loop coordination is out of scope (use a
DB-side dedup or a shared NATS dedup window for that).
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    from structlog.stdlib import BoundLogger

__all__ = ["DedupingConsumer"]

_DEFAULT_CAPACITY: int = 10_000


class DedupingConsumer[T]:
    """§20 H-009: consumer-side dedup of replay-prone event streams.

    Tracks the last ``capacity`` keys seen via ``key_fn``. Calls to
    :meth:`consume` invoke :meth:`_process` exactly once per distinct
    key within the ring window; subsequent duplicates are dropped
    silently with a DEBUG log entry.

    Subclass binding (T-218 ExecutionEvent dispatcher precedent):

    .. code-block:: python

        class ExecutionDispatcher(DedupingConsumer[ExecutionEvent]):
            def __init__(self, *, logger: BoundLogger, ...) -> None:
                super().__init__(
                    key_fn=lambda e: e.exchange_exec_id,
                    logger=logger,
                )
            async def _process(self, event: ExecutionEvent) -> None:
                ...  # business logic — runs OUTSIDE the ring lock

    Locking: the lock protects the **ring** (keys-seen tracking), not
    the **handler** (``_process``). Two distinct-key ``consume`` calls
    can run their handlers concurrently; two duplicate-key calls see
    one ``_process`` invocation. This decoupling means a slow
    ``_process`` does not block ring updates, but subclasses must be
    re-entrancy-safe at the ``_process`` level if concurrent handler
    invocation matters.
    """

    def __init__(
        self,
        *,
        key_fn: Callable[[T], str],
        capacity: int = _DEFAULT_CAPACITY,
        logger: BoundLogger | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {capacity}")
        self._key_fn = key_fn
        self._capacity = capacity
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = asyncio.Lock()
        # ``structlog.stdlib.get_logger(name)`` is a factory (returns a
        # fresh BoundLogger; structlog caches internally by name, not a
        # mutable module-level singleton — §N6 compliant). Subclasses
        # typically inject a service-bound logger via
        # ``packages.observability.get_logger(service, "system")``.
        self._logger: BoundLogger = (
            logger if logger is not None else structlog.stdlib.get_logger(__name__)
        )

    async def consume(self, message: T) -> None:
        """Run :meth:`_process` iff ``key_fn(message)`` is fresh.

        Duplicate keys (still resident in the ring) are dropped with a
        DEBUG log entry; ``_process`` is not invoked.
        """
        key = self._key_fn(message)
        async with self._lock:
            if key in self._seen:
                is_fresh = False
            else:
                is_fresh = True
                self._seen[key] = None
                if len(self._seen) > self._capacity:
                    self._seen.popitem(last=False)
        # Lock released — log + business handler run outside the ring
        # critical section so a slow _process does not block ring
        # updates. Per W#1 / W#4 (Gate-1 plan-reviewer guidance).
        if not is_fresh:
            self._logger.debug("dedup_dropped", key=key)
            return
        await self._process(message)

    async def _process(self, message: T) -> None:
        """Subclass override — handle a single distinct-key message."""
        raise NotImplementedError("Subclasses of DedupingConsumer must override _process")
