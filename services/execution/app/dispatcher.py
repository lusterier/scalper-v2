"""§9.5 line 1591 ExecutionEvent dispatcher (T-218a + T-218b).

Per-bot dispatcher consuming :meth:`ExchangeClient.stream_executions`.
Wraps T-210 :class:`packages.bus.dedup.DedupingConsumer` keyed on
``event.exchange_exec_id`` (H-009 ring; capacity from Settings).
T-218a ships the class skeleton + lifespan task wiring; T-218b owns
the :meth:`ExecutionDispatcher._process` body (orders lookup,
exec_type derivation, INSERT execution, UPDATE position_state, UPDATE
trade fees, T-219 close forward-pointer).

The ``run_dispatcher_for_bot`` task pumps the adapter's stream into
the dispatcher's :meth:`DedupingConsumer.consume` so duplicate-keyed
events are dropped before the body runs.

Lifespan ordering (per main.py reverse-shutdown contract):

1. ``bus.close()`` — drains placement subscriptions.
2. ``dispatcher_tasks`` cancel — dispatchers consume from
   ``adapter.stream_executions()``; cancelling them before the
   adapter prevents mid-iter raises (graceful stop).
3. ``ws_tasks`` + ``paper_consumer_tasks`` cancel.
4. ``adapter.close()`` per bot.
5. ``pool.close()``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from packages.bus.dedup import DedupingConsumer

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient
    from packages.core import BotId
    from packages.exchange.protocols import ExchangeClient
    from packages.exchange.types import ExecutionEvent


__all__ = ["ExecutionDispatcher", "run_dispatcher_for_bot"]


class ExecutionDispatcher(DedupingConsumer["ExecutionEvent"]):
    """§20 H-009 per-bot dedup ring keyed on :attr:`ExecutionEvent.exchange_exec_id`.

    Subclass of :class:`DedupingConsumer`. T-218a ships the ctor +
    skeleton; T-218b overrides :meth:`_process` body.

    Ctor DI per §N6: bot_id / pool / bus / bound_logger / capacity / now_fn.
    No module-level mutable state. ``now_fn`` injected for testable
    UTC timestamps (per §N1) — production lifespan wires
    ``lambda: datetime.now(UTC)``.

    Capacity comes from ``Settings.dispatch_dedup_capacity`` (default
    10000 per §9.5:1591 "ring buffer, size 10k"; configurable per §N9).
    """

    def __init__(
        self,
        *,
        bot_id: BotId,
        pool: asyncpg.Pool,
        bus: NatsClient,
        bound_logger: BoundLogger,
        capacity: int,
        now_fn: Callable[[], datetime],
    ) -> None:
        super().__init__(
            key_fn=lambda event: event.exchange_exec_id,
            capacity=capacity,
            logger=bound_logger,
        )
        self._bot_id = bot_id
        self._pool = pool
        self._bus = bus
        self._bound_logger = bound_logger
        self._now_fn = now_fn

    @property
    def bot_id(self) -> BotId:
        """Public read-only access to bound bot_id (used by run_dispatcher_for_bot logging)."""
        return self._bot_id

    async def _process(self, message: ExecutionEvent) -> None:
        """T-218b owns this body — orders lookup + exec_type derivation +
        INSERT execution + UPDATE position_state + UPDATE trade fees +
        T-219 close forward-pointer per OQ-1..OQ-5 (deferred from T-218a).
        """
        raise NotImplementedError(
            "T-218b: exec_type derivation + INSERT execution + UPDATE position_state "
            "+ fees backfill + T-219 close forward-pointer"
        )


async def run_dispatcher_for_bot(
    *,
    adapter: ExchangeClient,
    dispatcher: ExecutionDispatcher,
    bound_logger: BoundLogger,
) -> None:
    """Background task body — pump :meth:`ExchangeClient.stream_executions`
    into :meth:`ExecutionDispatcher.consume`.

    Lifecycle:

    * Normal flow: ``async for event in adapter.stream_executions()``
      delivers each event to dedup ring → ``_process`` body.
    * Cancellation (lifespan reverse-shutdown): :class:`asyncio.CancelledError`
      propagated cleanly without log emit (graceful stop signal).
    * Mid-flight stream failure (per-bot isolation; do NOT crash service):
      log ERROR ``execution.dispatcher_stream_terminated`` + re-raise.
      Lifespan gathers with ``return_exceptions=True`` so the failed
      task is reported but service stays up. Diverges from T-216a WG#7
      fail-fast (startup) — per-bot isolation > fail-fast for mid-flight.
      T-221 post-restart reconciliation is the recovery path.
    """
    try:
        async for event in adapter.stream_executions():
            await dispatcher.consume(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        bound_logger.error(
            "execution.dispatcher_stream_terminated",
            bot_id=dispatcher.bot_id,
            error=str(exc),
        )
        raise
