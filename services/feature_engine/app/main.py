"""FastAPI app factory + lifespan for feature-engine (§9.3, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

T-109 shipped pool + bus + ``/health`` / ``/ready`` / ``/metrics``;
T-110a/b/c shipped the consumer ports (BufferRegistry, infra, FeaturePipeline);
T-110d (this file) wires the composition root: build features → capacity_map →
BufferRegistry → pool (with JSONB codec init) → bus → FeaturePipeline →
acquire_handles → warmup_load → start_consuming → state attach. Reverse
on shutdown.

Composition split (mirrors T-100 / T-015a/T-015b2a):

* **Synchronous primitives** (Settings, structlog logger, Prometheus
  registry) instantiated in :func:`create_app` body and attached to
  ``app.state`` immediately so dependency providers in :mod:`.deps`
  see them before lifespan runs.
* **Asynchronous resources** (asyncpg :class:`~asyncpg.Pool`,
  :class:`packages.bus.NatsClient`, :class:`packages.features.buffers.BufferRegistry`,
  :class:`.pipeline.FeaturePipeline`) live in the lifespan and attach
  inside the ``async with`` block.

Lifespan order (load-bearing per ``docs/plans/T-110d.md`` Hand verification):

1. ``features_by_key = build_features()`` — T-110c hardcoded; T-111
   YAML loader replaces.
2. ``capacity_map`` = max ``warmup_candles`` per key.
3. ``registry = BufferRegistry(capacity_map)``.
4. ``pool = await create_pool(..., init=_register_jsonb_codec)`` —
   JSONB codec on every pool.acquire (T-110b Hand-off option (a)) so
   ``insert_feature(value_json=...)`` round-trips dict ↔ JSONB.
5. ``bus = NatsClient(...); await bus.connect()`` — JetStream context
   for kv_put + publish + subscribe.
6. ``pipeline = FeaturePipeline(...)`` — T-110c consumer.
7. ``pipeline.acquire_handles()`` — sync; allocates buffers via T-110a
   :class:`BufferRegistry.acquire` (0→1 transition, refcount 1).
8. ``await warmup_load(..., source=SOURCE_BINANCE)`` — pushes cagg
   history into the now-allocated buffers in time order.
9. ``await pipeline.start_consuming()`` — subscribes to
   ``market.ohlc.1m.>``. From this moment live frames welcome.
10. State attach: pool / bus / registry / pipeline → ``app.state``.

**Q11 race resolution** (T-110d plan): warmup MUST run between handle
acquisition and bus subscribe. If warmup ran before
:meth:`pipeline.acquire_handles`, ``BufferRegistry.push`` would
silent-drop on un-allocated buffers (T-110a Decision #2). If warmup
ran after :meth:`pipeline.start_consuming`, live frames could
interleave with warmup pushes and wedge out-of-order data into the
deque. The 7→8→9 sequence above closes both races.

Shutdown order (reverse, also load-bearing):

11. ``await pipeline.stop()`` — releases :class:`BufferHandle`
    instances. **Must run BEFORE** ``bus.close`` so per-feature
    consumer task teardown lands while the bus is still open
    (T-109 docstring pinned this; T-110d honours).
12. ``await bus.close()`` — drains tracked subscriptions, closes
    NATS connection.
13. ``await pool.close()`` — releases asyncpg connections.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from packages.bus import NatsClient
from packages.db import create_pool
from packages.features.buffers import BufferRegistry
from packages.observability import (
    configure,
    get_logger,
    make_metrics_asgi_app,
    make_registry,
)

from .auto_backfill import schedule_auto_backfills
from .config import Settings
from .constants import SOURCE_BINANCE
from .features_registry import build_features
from .health import router as health_router
from .pipeline import FeaturePipeline
from .warmup import warmup_load

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import asyncpg

__all__ = ["create_app"]


async def _register_jsonb_codec(conn: asyncpg.Connection) -> None:
    """Register the JSONB codec on a freshly-acquired asyncpg connection.

    Mirrors T-108 ``test_0004_migration:55-62`` per-connection pattern,
    lifted to pool-level init via :func:`packages.db.create_pool`'s
    ``init=`` callback (T-110d) so production
    :func:`~packages.db.queries.feature_engine.insert_feature`
    ``value_json=`` writes work without per-call codec registration.
    """
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` for feature-engine."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    configure(level=settings.log_level)
    logger = get_logger(settings.service_name, "system")
    registry_metrics = make_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring."""
        # 1-3. Build registry from feature_set (T-111: YAML-driven).
        features_by_key = build_features(settings.symbols)
        capacity_map = {
            key: max(feature.warmup_candles for _, feature in entries)
            for key, entries in features_by_key.items()
        }
        buffer_registry = BufferRegistry(capacity_map)

        # 4. asyncpg pool with JSONB codec init for T-110b value_json writes.
        pool = await create_pool(
            settings.database_url,
            application_name=settings.service_name,
            init=_register_jsonb_codec,
        )
        # 5. NATS bus.
        bus = NatsClient(
            servers=[settings.nats_url],
            name=settings.service_name,
            logger=logger,
        )
        await bus.connect()

        # 6. Pipeline DI.
        pipeline = FeaturePipeline(
            bus=bus,
            pool=pool,
            buffer_registry=buffer_registry,
            features_by_key=features_by_key,
            logger=logger,
        )
        # 7→8→9. Q11 race resolution: acquire BEFORE warmup BEFORE subscribe.
        pipeline.acquire_handles()
        await warmup_load(
            pool=pool,
            registry=buffer_registry,
            features_by_key=features_by_key,
            source=SOURCE_BINANCE,
            logger=logger,
        )
        await pipeline.start_consuming()

        # 9.5. T-518 — auto-backfill NEW features detected via YAML-diff vs
        # `feature_registry_seen` NATS KV bucket (ADR-0012). Fire-and-forget;
        # tasks tracked in app.state.auto_backfill_tasks for shutdown cancel.
        # Runs AFTER start_consuming so live-candle processing isn't blocked.
        auto_backfill_tasks: set[asyncio.Task[None]] = set()
        await schedule_auto_backfills(
            pool=pool,
            bus=bus,
            features_by_key=features_by_key,
            window_days=settings.backfill_window_days,
            source=SOURCE_BINANCE,
            logger=logger,
            background_tasks=auto_backfill_tasks,
        )

        # 10. State attach.
        app.state.pool = pool
        app.state.bus = bus
        app.state.buffer_registry = buffer_registry
        app.state.pipeline = pipeline
        app.state.auto_backfill_tasks = auto_backfill_tasks

        logger.info("service_started", http_port=settings.http_port)
        try:
            yield
        finally:
            # 11→12→13. Reverse shutdown.
            # T-518: cancel any in-flight auto-backfill tasks BEFORE
            # pipeline.stop() — pool/bus still open for already-running task
            # body cleanup (INSERT ON CONFLICT atomic per asyncpg).
            for task in list(auto_backfill_tasks):
                task.cancel()
            if auto_backfill_tasks:
                await asyncio.gather(*auto_backfill_tasks, return_exceptions=True)
            await pipeline.stop()
            await bus.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    app.state.settings = settings
    app.state.logger = logger

    app.include_router(health_router)
    app.mount("/metrics", make_metrics_asgi_app(registry_metrics))
    return app
