"""FastAPI app factory + lifespan for feature-engine (§9.3, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

This is the T-109 skeleton — pool + bus + ``/health`` / ``/ready`` /
``/metrics`` only. The actual computation loop (subscribe to
``market.ohlc.>``, dispatch to feature registry, persist to ``features``,
update NATS KV ``feature_latest``, publish ``features.updated.>``) lands
in T-110 on top of this scaffold. Mirrors T-100 (market-data-svc)
verbatim minus the ``BinanceWsClient`` / ``SubscriptionManager`` /
``OhlcPipeline`` / ``OhlcBackfill`` composition; see
``services/market_data/app/main.py`` for the full lifespan rationale
(holder pattern, race notes, reverse-order shutdown for an
external-WS-owning service).

Composition split (mirrors T-100 / T-015a/T-015b2a):

* **Synchronous primitives** (Settings, structlog logger, Prometheus
  registry) instantiated in :func:`create_app` body and attached to
  ``app.state`` immediately so dependency providers in :mod:`.deps`
  see them before lifespan runs (relevant for in-process unit tests
  that hit endpoints without lifespan startup).

* **Asynchronous resources** (asyncpg :class:`~asyncpg.Pool` and
  :class:`packages.bus.NatsClient`) live in the lifespan and attach to
  ``app.state`` inside the ``async with`` block. Teardown closes them
  in reverse order; ordering is load-bearing — bus first (drains
  in-flight publishes) then pool (so the bus drain can still execute
  any final lookups during graceful shutdown).

T-110 will extend the lifespan with its own
``await pipeline.stop()`` call before ``await bus.close()`` so per-feature
consumer task teardown lands while the bus is still open.
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
    """Build a configured :class:`FastAPI` for feature-engine.

    ``settings`` is injected for tests; in production it defaults to
    :class:`Settings` sourced from the process environment. A failure
    to validate env at this call site prevents uvicorn from ever
    binding the port (§5.11 fail-fast).
    """
    if settings is None:
        # Settings() reads env via pydantic-settings; mypy has no
        # plugin for env-sourcing, so required fields look "missing".
        settings = Settings()  # type: ignore[call-arg]

    # Observability bootstrap — happens before logger acquisition.
    configure(level=settings.log_level)

    # Single system-stream logger. T-109 emits no trading events
    # (skeleton only); a trading-stream logger gets added when T-110
    # lands the per-feature consumer that publishes
    # ``features.updated.>`` events.
    logger = get_logger(settings.service_name, "system")

    # Prometheus registry with default collectors only. Service
    # counters/histograms are added when concrete consumers surface
    # (no service metrics in T-109 per §0.8). The registry is
    # referenced again only by the /metrics ASGI mount below; not
    # exposed on app.state (no handler reads it).
    registry = make_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring."""
        # 1. asyncpg pool — packages.db.create_pool wrapper does DSN
        #    scheme validation + application_name injection (per-service
        #    log attribution in PG; T-110 will be the first writer
        #    against the features table that T-108 created).
        pool = await create_pool(
            settings.database_url,
            application_name=settings.service_name,
        )
        # 2. NATS bus — JetStream context acquired; subscriptions/
        #    publishes ready for T-110's market.ohlc.> consumer +
        #    features.updated.* publisher.
        bus = NatsClient(
            servers=[settings.nats_url],
            name=settings.service_name,
            logger=logger,
        )
        await bus.connect()

        app.state.pool = pool
        app.state.bus = bus

        logger.info("service_started", http_port=settings.http_port)
        try:
            yield
        finally:
            # Reverse order: bus.close drains in-flight publishes
            # against the still-open pool, then pool.close releases
            # asyncpg connections. T-110 will extend this with
            # pipeline.stop before bus.close so per-feature consumer
            # task teardown lands while the bus is still open.
            await bus.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    # Sync state attach happens here (not in lifespan) so deps.py
    # providers see typed primitives immediately after create_app()
    # returns. Defensive against tests that hit endpoints outside a
    # TestClient context and never enter the lifespan.
    app.state.settings = settings
    app.state.logger = logger

    app.include_router(health_router)
    app.mount("/metrics", make_metrics_asgi_app(registry))
    return app
