"""Reference-counted rolling-candle buffer registry (§9.3, H-014).

One in-memory :class:`collections.deque` per ``(symbol, interval)``
key, sized to the pre-declared capacity. Multiple features that share
the same ``(symbol, interval)`` (e.g., EMA-20, EMA-50, RSI-14 over
``("BTCUSDT", "15m")``) acquire individual :class:`BufferHandle`
instances on the same underlying buffer; the buffer is allocated on
the 0→1 acquire transition and deallocated on the 1→0 release
transition.

H-014 contract: when caller A holds a handle and caller B does the
same, A releasing does **not** dealloc B's view of the buffer; B
continues reading the same data until it too releases. v2 equivalent
of the v1 ``PriceManager`` refcount discipline; same shape as
:class:`packages.market.subscription.SubscriptionManager` (T-101c)
but for in-memory buffers in sync form. Hazard test:
``test_refcount_buffer_survives_one_holder_releasing``.

Pure domain — no :mod:`asyncio`, no NATS, no DB. Single-threaded
asyncio callers (T-110b feature-engine event loop) serialise access
via the event loop; sync methods never await, so no Lock is needed.
The :func:`packages.features.buffers` import path is the sole entry
point — :mod:`packages.features.__init__` deliberately does not
re-export ``BufferRegistry`` so the public feature API surface stays
narrow (this module is a T-110b composition-root dependency, not a
feature author's surface).

Push to a key that is unknown (not in ``capacity_map``) or has
refcount=0 is a silent no-op. This is defensive against T-110b's
NATS handler delivering a closed-candle for a symbol whose features
have not yet acquired (warmup-ordering race) or for a symbol the
service does not consume — both must fail safe rather than crash
the consumer task.
"""

from __future__ import annotations

import itertools
from collections import deque
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from packages.features.types import OhlcCandle


__all__ = ["BufferHandle", "BufferRegistry"]


class BufferRegistry:
    """Per-``(symbol, interval)`` refcounted buffer registry (H-014).

    See module docstring for the H-014 contract, sync-only rationale,
    and silent-drop policy.
    """

    def __init__(self, capacity_map: Mapping[tuple[str, str], int]) -> None:
        """Pin valid ``(symbol, interval)`` keys and their fixed capacities.

        ``capacity_map`` is computed by T-110b's composition root from
        the registered feature set: capacity per key is the maximum
        ``warmup_candles`` of all features consuming that key. The map
        is copied (not aliased) so later mutation by the caller cannot
        silently change buffer behaviour.
        """
        self._capacity_map: dict[tuple[str, str], int] = dict(capacity_map)
        self._buffers: dict[tuple[str, str], deque[OhlcCandle]] = {}
        self._counts: dict[tuple[str, str], int] = {}

    def acquire(self, symbol: str, interval: str) -> BufferHandle:
        """Bump refcount on ``(symbol, interval)``; return a fresh handle.

        Raises :class:`KeyError` if the key is not pinned in
        ``capacity_map`` — capacity must be declared at registry
        construction time (decision #3 / §0.8 anti-hypothetical).

        On the 0→1 transition the underlying deque is created with
        the pinned ``maxlen``; on subsequent acquires the existing
        deque is reused (sharing semantics, decision #1).
        """
        key = (symbol, interval)
        capacity = self._capacity_map[key]
        count = self._counts.get(key, 0)
        if count == 0:
            self._buffers[key] = deque(maxlen=capacity)
        self._counts[key] = count + 1
        return BufferHandle(self, symbol, interval)

    def push(self, symbol: str, interval: str, candle: OhlcCandle) -> None:
        """Append ``candle`` to the buffer for ``(symbol, interval)``.

        No-op if the key is unknown (not in ``capacity_map``) OR has
        refcount=0 (no acquired handles). Both conditions reduce to
        "no buffer exists in ``self._buffers``" so the lookup is the
        single guard. Buffer enforces fixed ``maxlen`` — the oldest
        candle is dropped on overflow (deque.append semantics).
        """
        buffer = self._buffers.get((symbol, interval))
        if buffer is None:
            return
        buffer.append(candle)

    def _release(self, symbol: str, interval: str) -> None:
        """Decrement refcount; on 1→0 dealloc the underlying buffer.

        Called by :meth:`BufferHandle.__exit__`. No-op if refcount is
        already 0 (defensive — should not happen given the one-shot
        handle contract, but guards against double-`__exit__` after
        the handle has already cleared its `_released` short-circuit).
        """
        key = (symbol, interval)
        count = self._counts.get(key, 0)
        if count == 0:
            return
        if count > 1:
            self._counts[key] = count - 1
            return
        del self._counts[key]
        del self._buffers[key]

    def _tail(self, symbol: str, interval: str, n: int) -> tuple[OhlcCandle, ...]:
        """Return the last ``<= n`` candles as an immutable snapshot.

        Buffer access for :meth:`BufferHandle.tail`. Slices the deque
        via :func:`itertools.islice` rather than ``list(deque)[-n:]``
        to avoid materialising the full history at every read — at
        capacity 50 the difference is negligible, but the idiom keeps
        the tail-read complexity O(n) rather than O(capacity) for
        future capacity growth.
        """
        buffer = self._buffers.get((symbol, interval))
        if buffer is None:
            return ()
        size = len(buffer)
        start = max(0, size - n)
        return tuple(itertools.islice(buffer, start, size))


class BufferHandle(AbstractContextManager["BufferHandle"]):
    """One reference into a ``(symbol, interval)`` buffer.

    Context-manager-style: ``__enter__`` returns ``self``, ``__exit__``
    decrements refcount via :meth:`BufferRegistry._release`. On the
    1→0 transition the registry deallocates the underlying buffer.
    After release, :meth:`tail` raises :class:`RuntimeError` and a
    second ``__exit__`` is an idempotent no-op (decision #10).

    Carries no internal refcount (I5) — exactly one handle = exactly
    one ref. The :attr:`_released` flag is a single bool, not a
    counter, and short-circuits both :meth:`tail` and double-exit.
    """

    def __init__(self, registry: BufferRegistry, symbol: str, interval: str) -> None:
        self._registry = registry
        self._symbol = symbol
        self._interval = interval
        self._released = False

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def interval(self) -> str:
        return self._interval

    def tail(self, n: int) -> tuple[OhlcCandle, ...]:
        """Return the last ``<= n`` candles as an immutable snapshot.

        Buffer with fewer than ``n`` candles returns the available
        prefix (shorter tuple) — :class:`Feature` callers raise
        :class:`packages.features.errors.FeatureUnderflowError` on
        insufficient warmup; the registry is dumb storage and does
        not enforce minimum length itself.

        Raises :class:`RuntimeError` if the handle has been released.
        """
        if self._released:
            msg = "handle released"
            raise RuntimeError(msg)
        return self._registry._tail(self._symbol, self._interval, n)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._released:
            return
        self._released = True
        self._registry._release(self._symbol, self._interval)
