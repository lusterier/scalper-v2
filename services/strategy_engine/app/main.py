"""FastAPI app factory + lifespan for strategy-engine (§9.4, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

T-309 ships the skeleton: plugin_registry + bot_config load → pool +
bus → FeatureResolver → state attach → ``/health`` / ``/ready`` /
``/metrics``. The signal consumer body is owned by T-310; this lifespan
attaches everything T-310 needs without subscribing to ``signals.validated``
yet.

Composition split (mirrors T-214 / T-109):

* **Synchronous primitives** (Settings, structlog logger, Prometheus
  registry) instantiated in :func:`create_app` body and attached to
  ``app.state`` immediately so dependency providers in :mod:`.deps`
  see them before lifespan runs.
* **Asynchronous resources** (asyncpg :class:`~asyncpg.Pool`,
  :class:`packages.bus.NatsClient`, :class:`packages.scoring.FeatureResolver`)
  live in the lifespan and attach inside the ``async with`` block.

Lifespan order (load-bearing):

1. ``plugin_registry = load_plugin_registry(Path(plugin_registry_path))``.
   Synchronous file-I/O. Failure crashes lifespan before NATS connection
   per fail-fast convention.
2. ``bot_config = load_bot_config(Path(bot_config_dir) / f"{bot_id}.yaml",
   plugin_registry=plugin_registry)``. **plugin_registry MUST precede
   bot_config** — kwarg dependency; reverse is NameError. The bot YAML
   may reference plugin conditions whose ``(name, version)`` resolves
   against the registry.
3. ``pool = await create_pool(database_url, application_name="strategy-engine")``.
4. ``bus = NatsClient(...); await bus.connect()``.
5. ``resolver = FeatureResolver(bus=bus, pool=pool, bound_logger=logger)``.
6. State attach: pool / bus / plugin_registry / bot_config / resolver →
   ``app.state``.

Shutdown order (reverse, load-bearing):

7. ``await bus.close()`` — drains tracked subscriptions, closes NATS.
   **Must run BEFORE** ``pool.close()`` so any in-flight publish that
   touches the pool finishes against an open pool. T-310 will publish
   ``OrderRequest`` post ``scoring_evaluations`` INSERT — the publish-
   after-persist contract (T-200 Q2) inherits this order.
8. ``await pool.close()`` — releases asyncpg connections.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fastapi import FastAPI

from packages.bus import NatsClient
from packages.db import create_pool
from packages.observability import (
    configure,
    get_logger,
    make_metrics_asgi_app,
)
from packages.scoring import FeatureResolver, load_bot_config
from packages.scoring.registry import load_plugin_registry

from .config import Settings
from .consumer import make_signal_handler
from .health import router as health_router
from .kill_switch_reconcile import reconcile_kill_switch_on_startup
from .metrics import build_registry, build_strategy_engine_metrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from packages.core import BotId

__all__ = ["create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` for strategy-engine."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    configure(level=settings.log_level)
    logger = get_logger(settings.service_name, "system")
    trading_logger = get_logger(settings.service_name, "trading")
    audit_logger = get_logger(settings.service_name, "audit")
    registry_metrics = build_registry()
    metrics = build_strategy_engine_metrics(registry_metrics)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring."""
        # 1. Plugin registry (must precede bot_config — kwarg dependency).
        plugin_registry = load_plugin_registry(Path(settings.plugin_registry_path))

        # 2. Bot config — references plugin_registry for any plugin conditions.
        bot_config_path = Path(settings.bot_config_dir) / f"{settings.bot_id}.yaml"
        bot_config = load_bot_config(bot_config_path, plugin_registry=plugin_registry)

        # 3. asyncpg pool.
        pool = await create_pool(
            settings.database_url,
            application_name=settings.service_name,
        )
        # 4. NATS bus.
        bus = NatsClient(
            servers=[settings.nats_url],
            name=settings.service_name,
            logger=logger,
        )
        await bus.connect()

        # 5. FeatureResolver — KV → DB → staleness check per §10.3.
        resolver = FeatureResolver(bus=bus, pool=pool, bound_logger=logger)

        # 6. State attach (BEFORE subscribe — handler closure captures bot_config + resolver).
        app.state.pool = pool
        app.state.bus = bus
        app.state.plugin_registry = plugin_registry
        app.state.bot_config = bot_config
        app.state.resolver = resolver

        # 6.5. T-525a1 — re-evaluate the persistent kill-switch latch at startup
        # (H-027). AFTER pool create, BEFORE subscribe so a stale prior-UTC-day
        # daily latch is cleared (or a same-day stop retained + warn-logged)
        # before the first signal can be consumed. Best-effort: never raises
        # into lifespan (the per-signal gate, T-525a2, re-evaluates anyway).
        await reconcile_kill_switch_on_startup(
            pool=pool,
            bot_id=settings.bot_id,
            now_fn=lambda: datetime.now(UTC),
            system_logger=logger,
        )

        # 7. T-310b — subscribe to signals.validated with bot-bound handler.
        # Single subscription per process (one container per bot per §9.4:1530).
        # Subscribe failure crashes lifespan (mirror T-216a WG#7 fail-fast).
        signal_handler = make_signal_handler(
            bot_id=cast("BotId", settings.bot_id),
            bot_config=bot_config,
            resolver=resolver,
            pool=pool,
            bus=bus,
            trading_logger=trading_logger,
            system_logger=logger,
            audit_logger=audit_logger,
            now_fn=lambda: datetime.now(UTC),
            max_signal_age_seconds=settings.signal_max_age_seconds,
            metrics=metrics,
        )
        await bus.subscribe("signals.validated", signal_handler)

        logger.info(
            "service_started",
            http_port=settings.http_port,
            bot_id=settings.bot_id,
            rules_count=len(bot_config.scoring.rules),
        )
        try:
            yield
        finally:
            # 7→8. Reverse shutdown: bus first (drain in-flight publishes),
            # pool second (publish-after-persist per T-200 Q2).
            await bus.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    app.state.settings = settings
    app.state.logger = logger

    app.include_router(health_router)
    app.mount("/metrics", make_metrics_asgi_app(registry_metrics))
    return app
