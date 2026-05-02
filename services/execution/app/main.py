"""FastAPI app factory + lifespan for execution-service (§9.5, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

T-214 shipped the skeleton: pool + bus + ``/health`` / ``/ready`` /
``/metrics``. T-215 wired the adapter pool composition root + rate
limiter. T-216a (this revision) registers per-bot
``orders.requests.<bot_id>`` subscriptions at lifespan step 6 (one per
bot in ``app.state.adapters``); T-216a handler stops at NotImplementedError
post-fill_price (T-216b owns SL+TP+persist+events). Subscribe failure
at lifespan = service crash (mirror Settings startup-validation
invariant per WG#7); silent partial-failure (1/N bots subscribed) is
NOT acceptable.

Remaining F2 work:

* T-216b — extend placement.py with SL+TP+persist+events post-fill_price.
* T-217 — :class:`PositionLifecycle` FSM monitor task per trade.
* T-218 — :class:`DedupingConsumer` execution dispatcher.
* T-219 — cumulative-delta P&L close flow.
* T-220 — APScheduler-driven P&L audit loop.
* T-221 — post-restart reconciliation (H-020).

Composition split (mirrors T-100 / T-109):

* **Synchronous primitives** (Settings, structlog logger, Prometheus
  registry) instantiated in :func:`create_app` body and attached to
  ``app.state`` immediately so dependency providers in :mod:`.deps`
  see them before lifespan runs.
* **Asynchronous resources** (asyncpg :class:`~asyncpg.Pool`,
  :class:`packages.bus.NatsClient`) live in the lifespan and attach
  inside the ``async with`` block.

Lifespan order (T-216a + T-218a — 8 steps):

1. ``pool = await create_pool(database_url, application_name="execution-service")``.
2. ``bus = NatsClient(...); await bus.connect()``.
3. ``rate_limiter = SharedRateLimiter(bus=bus, **rate_limit_kwargs)`` — per ADR-0003.
4. ``adapter_pool = await build_adapter_pool(...)`` — reads active bots,
   per-bot constructs adapter, spawns ws/paper tasks (T-215; H-022, ADR-0004).
5. **T-216a**: per-bot ``orders.requests.<bot_id>`` subscription loop —
   ``for bot_id, adapter in adapter_pool.adapters.items(): await
   bus.subscribe(subject_for_orders_request(bot_id), make_per_bot_handler(...))``.
   Subscribe failure at lifespan = service crash (WG#7 fail-fast).
6. **T-218a**: per-bot ``ExecutionDispatcher`` task — one
   ``asyncio.create_task(run_dispatcher_for_bot(...))`` per
   ``(bot_id, adapter)``. Each task pumps ``adapter.stream_executions()``
   into a ``DedupingConsumer[ExecutionEvent]`` keyed on ``exchange_exec_id``
   (H-009 ring; capacity from ``Settings.dispatch_dedup_capacity``).
7. State attach: pool / bus / rate_limiter / adapters / ws_tasks /
   paper_consumer_tasks / dispatcher_tasks → ``app.state``.

Shutdown order (reverse, load-bearing):

8. ``await bus.close()`` — drains tracked subscriptions (incl. per-bot
   ``orders.requests.<bot_id>``), closes NATS connection. **Must run BEFORE**
   ``adapter.close`` / ``pool.close`` so any in-flight bus publish that
   touches downstream components finishes against open infra.
9. Cancel + gather ``dispatcher_tasks`` — they consume from
   ``adapter.stream_executions()``; cancelling them BEFORE adapter
   prevents mid-iter raises (graceful stop signal via CancelledError).
10. Cancel + gather ws_tasks + paper_consumer_tasks (drain backgrounds).
11. ``await adapter.close()`` per bot (Bybit closes ws + httpx; Paper no-op).
12. ``await pool.close()`` — releases asyncpg connections.

T-216 publish-after-persist contract (T-200 Q2) inherits this order:
the pool stays open until the bus has fully drained, so the publish
side of `publish-after-persist` cannot race against pool teardown.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import FastAPI

from packages.bus import NatsClient
from packages.bus.schemas.orders import subject_for_orders_request
from packages.db import create_pool
from packages.exchange.rate_limiter import SharedRateLimiter
from packages.observability import (
    configure,
    get_logger,
    make_metrics_asgi_app,
    make_registry,
)

from .config import Settings
from .dispatcher import ExecutionDispatcher, run_dispatcher_for_bot
from .health import router as health_router
from .placement import make_per_bot_handler
from .pool import build_adapter_pool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ["create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` for execution-service."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    configure(level=settings.log_level)
    logger = get_logger(settings.service_name, "system")
    registry_metrics = make_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring."""
        # 1. asyncpg pool.
        pool = await create_pool(
            settings.database_url,
            application_name=settings.service_name,
        )
        # 2. NATS bus.
        bus = NatsClient(
            servers=[settings.nats_url],
            name=settings.service_name,
            logger=logger,
        )
        await bus.connect()

        # 3. SharedRateLimiter (per ADR-0003).
        rate_limiter = SharedRateLimiter(
            bus=bus,
            orders_rate=settings.rate_limit_orders_rate,
            orders_capacity=settings.rate_limit_orders_capacity,
            positions_rate=settings.rate_limit_positions_rate,
            positions_capacity=settings.rate_limit_positions_capacity,
            ip_global_rate=settings.rate_limit_ip_global_rate,
            ip_global_capacity=settings.rate_limit_ip_global_capacity,
            pause_ms=settings.rate_limit_pause_ms,
        )

        # 4. Adapter pool composition (active bots → per-bot adapter + tasks).
        adapter_pool = await build_adapter_pool(
            pool=pool,
            bus=bus,
            rate_limiter=rate_limiter,
            settings=settings,
            bound_logger=logger,
        )

        # T-217a — single shared registry across all bots; trade_id is BIGSERIAL global-unique.
        position_lifecycle_tasks: dict[int, asyncio.Task[None]] = {}

        # 5. Per-bot orders.requests.<bot_id> subscription (T-216a + T-216b2 wrap).
        # The handler returned by make_per_bot_handler is the OrderRequestDedupConsumer.consume
        # bound method (H-009 per-bot ring; capacity from Settings). Subscribe failure
        # here crashes the lifespan (WG#7 fail-fast).
        for bot_id, adapter in adapter_pool.adapters.items():
            handler = make_per_bot_handler(
                bot_id=bot_id,
                adapter=adapter,
                bus=bus,
                logger=logger,
                pool=pool,
                dedup_capacity=settings.execution_orders_dedup_capacity,
                now_fn=lambda: datetime.now(UTC),
                fill_price_retry_attempts=settings.execution_fill_price_retry_attempts,
                fill_price_retry_backoff_s=settings.execution_fill_price_retry_backoff_s,
                position_lifecycle_tasks=position_lifecycle_tasks,
                position_poll_interval_s=settings.position_poll_interval_s,
                position_poll_stale_ticks=settings.position_poll_stale_ticks,
            )
            await bus.subscribe(subject_for_orders_request(bot_id), handler)

        # T-219 — per-sub-account asyncio.Lock registry per ADR-0006 D4.
        # Multiple bots may share a sub_account (per ADR-0004 H-022 family);
        # they share the same Lock instance to serialize closed-pnl snapshot pairs.
        # Lock is keyed on sub_account string (NOT bot_id). Paper adapters use
        # bot_id-as-sub_account synonym per Decision #8.
        closed_pnl_locks: dict[str, asyncio.Lock] = {}

        def _resolve_sub_account(adapter_obj: object) -> str:
            sub_account = getattr(adapter_obj, "_sub_account", None)
            if sub_account is None:
                bot_id_attr = getattr(adapter_obj, "_bot_id", None)
                if bot_id_attr is None:
                    msg = "adapter has no _sub_account or _bot_id attribute"
                    raise RuntimeError(msg)
                return str(bot_id_attr)
            return str(sub_account)

        # 6. Per-bot ExecutionDispatcher tasks (T-218a; H-009 per-bot dedup ring).
        dispatcher_tasks: list[asyncio.Task[None]] = []
        for bot_id, adapter in adapter_pool.adapters.items():
            sub_account = _resolve_sub_account(adapter)
            if sub_account not in closed_pnl_locks:
                closed_pnl_locks[sub_account] = asyncio.Lock()
            dispatcher = ExecutionDispatcher(
                bot_id=bot_id,
                pool=pool,
                bus=bus,
                bound_logger=logger,
                capacity=settings.dispatch_dedup_capacity,
                now_fn=lambda: datetime.now(UTC),
                adapter=adapter,
                sub_account=sub_account,
                closed_pnl_lock=closed_pnl_locks[sub_account],
                closed_pnl_post_close_sleep_s=settings.execution_closed_pnl_post_close_sleep_s,
            )
            task = asyncio.create_task(
                run_dispatcher_for_bot(
                    adapter=adapter,
                    dispatcher=dispatcher,
                    bound_logger=logger,
                ),
                name=f"dispatcher_{bot_id}",
            )
            dispatcher_tasks.append(task)

        # 7. State attach.
        app.state.pool = pool
        app.state.bus = bus
        app.state.rate_limiter = rate_limiter
        app.state.adapters = adapter_pool.adapters
        app.state.ws_tasks = adapter_pool.ws_tasks
        app.state.paper_consumer_tasks = adapter_pool.paper_consumer_tasks
        app.state.dispatcher_tasks = dispatcher_tasks
        app.state.position_lifecycle_tasks = position_lifecycle_tasks
        app.state.closed_pnl_locks = closed_pnl_locks

        logger.info(
            "service_started",
            http_port=settings.http_port,
            bots_loaded=len(adapter_pool.adapters),
        )
        try:
            yield
        finally:
            # Reverse shutdown:
            #   bus.close → position_lifecycle_tasks cancel (T-217a; monitors write
            #   monitor-only fields, dispatcher writes fill-flow fields; column-disjoint
            #   UPDATEs are MVCC-safe but ordering keeps shutdown's audit log monotonic) →
            #   dispatcher_tasks cancel (consume from adapter.stream_*; must drain before
            #   adapter.close pulls the WS) → ws_tasks cancel → adapter.close → pool.close.
            await bus.close()
            for task in position_lifecycle_tasks.values():
                task.cancel()
            if position_lifecycle_tasks:
                await asyncio.gather(*position_lifecycle_tasks.values(), return_exceptions=True)
            for task in dispatcher_tasks:
                task.cancel()
            if dispatcher_tasks:
                await asyncio.gather(*dispatcher_tasks, return_exceptions=True)
            background_tasks = [
                *adapter_pool.ws_tasks,
                *adapter_pool.paper_consumer_tasks,
            ]
            for task in background_tasks:
                task.cancel()
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            for adapter in adapter_pool.adapters.values():
                await adapter.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    app.state.settings = settings
    app.state.logger = logger

    app.include_router(health_router)
    app.mount("/metrics", make_metrics_asgi_app(registry_metrics))
    return app
