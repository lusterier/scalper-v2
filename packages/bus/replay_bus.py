"""In-process timestamp-ordered pub/sub for backtest replay (T-502 / brief §12.2:1953).

BRIEF §12.2:1953 verbatim: *"`ReplayBus`: in-process NATS-compatible
publish/subscribe; messages delivered in timestamp order."*

ReplayBus mirrors the :class:`packages.bus.client.NatsClient` interface
**subset** that T-507 CLI orchestrator uses (`publish + subscribe +
close`) so consumer code (strategy-engine + execution-service) swaps
NatsClient for ReplayBus during backtest replay without modification —
both accept :class:`packages.bus.envelope.MessageEnvelope` payloads + the
same `subject + handler` shapes. Live NATS is replaced for replay; live
NatsClient remains the wire-protocol transport for production.

Algorithm: heapq-based priority queue keyed on `envelope.published_at`.
Producers (T-503 HistoricalOHLCSource + T-504 HistoricalSignalSource +
others) publish historical messages with `published_at` set to the
historical timestamp (e.g. `OHLCRow.bucket_start`); ReplayBus.run_until_empty
drains the heap min-first → matches subject against subscriber patterns
→ invokes handler(s) in true chronological order regardless of producer
yield order.

Heap tuple shape per WG#2: `(timestamp, insertion_seq, subject, envelope)`.
The integer `insertion_seq` second key BLOCKS heapq from comparing
MessageEnvelope (Pydantic `frozen=True` model has no `__lt__`; heapq
would raise TypeError on same-timestamp tie). Stable: same-timestamp
messages deliver in publish order.

Subject matching: NATS subset — exact + `*` (single-token wildcard) +
`>` (multi-token tail wildcard). NATS server-side native matching is
not exposed as Python helper, so reimplemented minimally here (~10 LOC).

Deviation from NatsClient (operator-decision F5 scope): T-502 has NO
logger DI. Handler exceptions are silent-swallowed (mirror NatsClient
`_dispatch:259-267` swallow but without the `bus_handler_failed` log).
F5+ logger DI accepted if observability requires — minimal additive change.
"""

from __future__ import annotations

import contextlib
import heapq
from dataclasses import dataclass
from typing import TYPE_CHECKING

from packages.core import idempotent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from packages.bus.envelope import MessageEnvelope

    type Handler = Callable[[MessageEnvelope], Awaitable[None]]


__all__ = ["ReplayBus", "ReplaySubscription"]


@dataclass(slots=True)
class ReplaySubscription:
    """Subscription handle returned by :meth:`ReplayBus.subscribe`.

    Set ``active = False`` to suspend handler invocation without removing the
    subscription from the bus (simpler than full unsubscribe; mirrors how
    operator-driven backtest may pause individual consumers in F5+).
    """

    subject_pattern: str
    handler: Handler
    active: bool = True


def _subject_matches(pattern: str, subject: str) -> bool:
    """NATS subject-match subset: exact + ``*`` single-token + ``>`` multi-token tail.

    * ``"a.b"`` vs ``"a.b"`` → True.
    * ``"a.*.c"`` vs ``"a.x.c"`` → True; ``"a.*.c"`` vs ``"a.x.y.c"`` → False.
    * ``"a.>"`` vs ``"a.x"`` / ``"a.x.y.z"`` → True (tail wildcard absorbs rest).
    * ``"a.b"`` vs ``"a.b.c"`` → False (token count must match without wildcard).
    """
    pat_tokens = pattern.split(".")
    sub_tokens = subject.split(".")
    for i, pt in enumerate(pat_tokens):
        if pt == ">":
            return True  # tail wildcard absorbs rest
        if i >= len(sub_tokens):
            return False
        if pt == "*":
            continue  # single-token wildcard matches any one token
        if pt != sub_tokens[i]:
            return False
    return len(pat_tokens) == len(sub_tokens)


class ReplayBus:
    """In-process NATS-compatible timestamp-ordered pub/sub for replay.

    See module docstring for full algorithm + heap tuple shape rationale +
    NatsClient interface deviation note.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, str, MessageEnvelope]] = []
        self._subscriptions: list[ReplaySubscription] = []
        self._insertion_seq = 0
        self._closed = False

    async def publish(self, subject: str, envelope: MessageEnvelope) -> None:
        """Heap-push ``(envelope.published_at, seq, subject, envelope)``.

        Raises ``RuntimeError`` on closed bus.
        """
        if self._closed:
            msg = "ReplayBus: publish on closed bus"
            raise RuntimeError(msg)
        heapq.heappush(
            self._heap,
            (envelope.published_at, self._insertion_seq, subject, envelope),
        )
        self._insertion_seq += 1

    async def subscribe(self, subject_pattern: str, handler: Handler) -> ReplaySubscription:
        """Register handler on subject pattern; return handle for later sub.active=False.

        T-507a: ``async def`` to satisfy :class:`packages.bus.BusProtocol`
        signature parity with :meth:`packages.bus.NatsClient.subscribe`.
        Body has no actual I/O (pure dict registration); the ``async``
        marker is interface compliance only — all 3 production call-sites
        already ``await bus.subscribe(...)``.

        Raises ``RuntimeError`` on closed bus.
        """
        if self._closed:
            msg = "ReplayBus: subscribe on closed bus"
            raise RuntimeError(msg)
        sub = ReplaySubscription(subject_pattern=subject_pattern, handler=handler)
        self._subscriptions.append(sub)
        return sub

    async def run_until_empty(self) -> None:
        """Drain heap; pop min-timestamp → match subject → invoke handler(s).

        Handler exceptions are silent-swallowed (mirror NatsClient _dispatch
        swallow). Drain semantic — after returning, additional publish() +
        subsequent run_until_empty() drains the new content (NOT a one-shot
        lifecycle).
        """
        while self._heap:
            _ts, _seq, subject, envelope = heapq.heappop(self._heap)
            for sub in self._subscriptions:
                if not sub.active:
                    continue
                if not _subject_matches(sub.subject_pattern, subject):
                    continue
                # Mirror NatsClient _dispatch silent-swallow — handler exception
                # must not kill drain. F5+ logger DI accepted if observability
                # requires (per OQ; module docstring deviation note).
                with contextlib.suppress(Exception):
                    await sub.handler(envelope)

    async def close(self) -> None:
        """Mark bus closed + clear heap and subscribers.

        Idempotent — second call is no-op.
        """
        if self._closed:
            return
        self._closed = True
        self._subscriptions.clear()
        self._heap.clear()

    @idempotent
    async def kv_get(self, bucket: str, key: str) -> tuple[bytes, int] | None:
        """T-507a: Replay has no KV state — always return ``None``.

        :class:`packages.scoring.FeatureResolver` ``_try_kv`` handles
        ``None`` via DB-fallback per
        ``packages/scoring/resolver.py:181-182``. Args ignored;
        signature matches :meth:`packages.bus.NatsClient.kv_get` for
        :class:`packages.bus.BusProtocol` satisfaction.

        ``@idempotent`` per symmetry with NatsClient.kv_get
        (`packages/bus/client.py:277`) — reads are replay-safe by
        definition.
        """
        return None

    async def kv_put(self, bucket: str, key: str, value: bytes) -> int:
        """T-507a: Replay does not support KV writes — fail loud per §0.4.

        ``@idempotent`` / ``@non_idempotent`` decorator skipped because
        the method unconditionally raises — failure semantic dominates.
        """
        msg = "ReplayBus.kv_put: KV writes are not supported in replay mode"
        raise NotImplementedError(msg)

    async def kv_update(self, bucket: str, key: str, value: bytes, last_revision: int) -> int:
        """T-507a: Replay does not support KV CAS — fail loud per §0.4."""
        msg = "ReplayBus.kv_update: KV CAS writes are not supported in replay mode"
        raise NotImplementedError(msg)
