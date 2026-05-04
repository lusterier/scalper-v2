"""FastAPI app factory + lifespan for analytics-api (§9.6, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

T-400 ships the skeleton: pool + bus → state attach → ``/health`` /
``/ready`` / ``/metrics``. All ``/api/*`` REST endpoints + the
``/events/stream`` SSE multiplexed stream are owned by T-401..T-408 per
§0.8 anti-hypothetical; this lifespan attaches what those tasks will
share without subscribing to NATS or registering any read handlers yet.

analytics-api is a single-instance singleton per BRIEF §2.2:234 (unlike
per-bot strategy-engine), so there is no ``BOT_ID`` env var, no per-bot
config load, and no per-bot subscription loop in this lifespan.

Composition split (mirrors T-309 / T-214 / T-109):

* **Synchronous primitives** (Settings, structlog logger, Prometheus
  registry) instantiated in :func:`create_app` body and attached to
  ``app.state`` immediately so dependency providers in :mod:`.deps`
  see them before lifespan runs.
* **Asynchronous resources** (asyncpg :class:`~asyncpg.Pool`,
  :class:`packages.bus.NatsClient`) live in the lifespan and attach
  inside the ``async with`` block.

Lifespan order (load-bearing):

1. ``pool = await create_pool(database_url, application_name="analytics-api")``.
2. ``bus = NatsClient(...); await bus.connect()``.
3. State attach: pool / bus → ``app.state``.

Shutdown order (reverse, load-bearing):

4. ``await bus.close()`` — drains tracked subscriptions, closes NATS.
   **Must run BEFORE** ``pool.close()`` so any in-flight publish that
   touches the pool finishes against an open pool. T-408 SSE handler
   will fan out NATS messages while reading from PG; closing bus first
   drains those before pool teardown. Inheriting the convention from
   strategy-engine main.py:152-153 / execution main.py:306-329 /
   feature-engine main.py:177-180.
5. ``await pool.close()`` — releases asyncpg connections.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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

from .analytics_cache import AnalyticsCache
from .config import Settings
from .health import router as health_router
from .routers.analytics import router as analytics_router
from .routers.audit import router as audit_router
from .routers.backtests import router as backtests_router
from .routers.bots import router as bots_router
from .routers.configs import router as configs_router
from .routers.features import router as features_router
from .routers.positions import router as positions_router
from .routers.scoring import router as scoring_router
from .routers.signals import router as signals_router
from .routers.symbol_map import router as symbol_map_router
from .routers.trades import router as trades_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

__all__ = ["create_app"]


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register the JSONB codec on a freshly-acquired asyncpg connection.

    Mirrors feature-engine T-110d pattern. T-401a queries (`select_all_bots`
    + `select_bot_by_id`) read the ``meta JSONB`` column on ``bots``;
    without this codec asyncpg returns JSONB as a raw string and the
    dict round-trip in :func:`packages.db.queries.analytics._row_to_bot_detail`
    falls back to ``{}``. Same default needed for T-401b symbol_map +
    T-405 audit_events readers.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` for analytics-api."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    configure(level=settings.log_level)
    logger = get_logger(settings.service_name, "system")
    registry_metrics = make_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring."""
        # 1. asyncpg pool with JSONB codec init for `meta` round-trip.
        pool = await create_pool(
            settings.database_url,
            application_name=settings.service_name,
            init=_register_jsonb_codec,
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
        # T-401b — now_fn injection point for audit-row timestamps;
        # tests monkey-patch via `client.app.state.now_fn = lambda: FIXED_NOW`.
        app.state.now_fn = lambda: datetime.now(UTC)
        # T-406 — in-memory analytics cache (Monte-Carlo only per OQ-2 default A).
        # Lifespan-owned per process; F4 single-process scope per §3.1.
        app.state.analytics_cache = AnalyticsCache()

        logger.info(
            "service_started",
            http_port=settings.http_port,
        )
        try:
            yield
        finally:
            # Reverse shutdown: bus first (drain in-flight publishes),
            # pool second (publish-after-persist per T-200 Q2).
            await bus.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    app.state.settings = settings
    app.state.logger = logger

    app.include_router(health_router)
    app.include_router(bots_router)
    app.include_router(symbol_map_router)
    app.include_router(positions_router)
    app.include_router(trades_router)
    app.include_router(signals_router)
    app.include_router(scoring_router)
    app.include_router(features_router)
    app.include_router(configs_router)
    app.include_router(audit_router)
    app.include_router(analytics_router)
    app.include_router(backtests_router)
    app.mount("/metrics", make_metrics_asgi_app(registry_metrics))
    return app
