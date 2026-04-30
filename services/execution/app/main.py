"""FastAPI app factory + lifespan for execution-service (§9.5, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

T-214 shipped the skeleton: pool + bus + ``/health`` / ``/ready`` /
``/metrics``. T-215 (this revision) wires the adapter pool composition
root: SharedRateLimiter + active-bots load + per-bot adapter
construction + background task spawning. Remaining F2 work:

* T-216 — order placement pipeline subscribes to
  ``orders.requests.<bot_id>`` and emits orders.events / trading_events.
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

Lifespan order (T-215 — 6 steps):

1. ``pool = await create_pool(database_url, application_name="execution-service")``.
2. ``bus = NatsClient(...); await bus.connect()``.
3. ``rate_limiter = SharedRateLimiter(bus=bus, **rate_limit_kwargs)`` — per ADR-0003.
4. ``result = await build_adapter_pool(pool, bus, rate_limiter, settings, logger)``
   — reads active bots, per-bot constructs adapter (Bybit live/testnet
   or PaperExchange), spawns ws_tasks / paper_consumer_tasks (H-022 per-bot
   creds, ADR-0004 sub_account env source).
5. State attach: pool / bus / rate_limiter / adapters / ws_tasks /
   paper_consumer_tasks → ``app.state``.

Shutdown order (reverse, load-bearing):

6. ``await bus.close()`` — drains tracked subscriptions, closes NATS
   connection. **Must run BEFORE** ``adapter.close`` / ``pool.close`` so any
   in-flight bus publish that touches downstream components finishes
   against open infra.
7. Cancel + gather ws_tasks + paper_consumer_tasks (drain backgrounds).
8. ``await adapter.close()`` per bot (Bybit closes ws + httpx; Paper no-op).
9. ``await pool.close()`` — releases asyncpg connections.

T-216 publish-after-persist contract (T-200 Q2) inherits this order:
the pool stays open until the bus has fully drained, so the publish
side of `publish-after-persist` cannot race against pool teardown.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from packages.bus import NatsClient
from packages.db import create_pool
from packages.exchange.rate_limiter import SharedRateLimiter
from packages.observability import (
    configure,
    get_logger,
    make_metrics_asgi_app,
    make_registry,
)

from .config import Settings
from .health import router as health_router
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

        # 5. State attach.
        app.state.pool = pool
        app.state.bus = bus
        app.state.rate_limiter = rate_limiter
        app.state.adapters = adapter_pool.adapters
        app.state.ws_tasks = adapter_pool.ws_tasks
        app.state.paper_consumer_tasks = adapter_pool.paper_consumer_tasks

        logger.info(
            "service_started",
            http_port=settings.http_port,
            bots_loaded=len(adapter_pool.adapters),
        )
        try:
            yield
        finally:
            # Reverse shutdown: bus → cancel tasks → adapter.close → pool.close.
            await bus.close()
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
