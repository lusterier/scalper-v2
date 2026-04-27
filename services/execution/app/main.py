"""FastAPI app factory + lifespan for execution-service (§9.5, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

T-214 ships the skeleton: pool + bus + ``/health`` / ``/ready`` /
``/metrics``. Pure scaffolding — no adapter pool, no order pipeline,
no FSM, no reconciliation. Each of those lands in its owner F2 task:

* T-205 — shared rate limiter wires into lifespan.
* T-215 — adapter pool composition root extends lifespan with
  ``bots``-table read + per-bot env-key lookup (H-022) +
  ``app.state.adapters: dict[BotId, ExchangeClient]`` attach.
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

Lifespan order (T-214 minimum — 3 steps):

1. ``pool = await create_pool(database_url, application_name="execution-service")``.
2. ``bus = NatsClient(servers=[nats_url], name="execution-service",
   logger=logger); await bus.connect()``.
3. State attach: pool / bus → ``app.state``.

T-215+ will extend this to a 5-7-step composition (rate limiter →
adapter pool → dispatcher → scheduler → reconciliation). T-214 keeps
the order minimal.

Shutdown order (reverse, load-bearing):

4. ``await bus.close()`` — drains tracked subscriptions, closes NATS
   connection. **Must run BEFORE** ``pool.close`` so any in-flight
   bus publish that touches the pool finishes against an open pool.
5. ``await pool.close()`` — releases asyncpg connections.

T-216 publish-after-persist contract (T-200 Q2) inherits this order:
the pool stays open until the bus has fully drained, so the publish
side of `publish-after-persist` cannot race against pool teardown.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from packages.bus import NatsClient
from packages.db import create_pool
from packages.observability import (
    configure,
    get_logger,
    make_metrics_asgi_app,
    make_registry,
)

from .config import Settings
from .health import router as health_router

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

        # 3. State attach.
        app.state.pool = pool
        app.state.bus = bus

        logger.info("service_started", http_port=settings.http_port)
        try:
            yield
        finally:
            # 4→5. Reverse shutdown: bus before pool.
            await bus.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    app.state.settings = settings
    app.state.logger = logger

    app.include_router(health_router)
    app.mount("/metrics", make_metrics_asgi_app(registry_metrics))
    return app
