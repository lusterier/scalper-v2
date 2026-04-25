"""FastAPI app factory + lifespan for market-data-svc (§9.2, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

Composition split (mirrors signal-gateway T-015a/T-015b2a):

* **Synchronous primitives** (Settings, structlog logger, Prometheus
  registry) instantiated in the ``create_app`` body and attached to
  ``app.state`` immediately so dependency providers in :mod:`.deps`
  see them before lifespan runs (relevant for in-process unit tests
  that hit endpoints without lifespan startup).

* **Asynchronous resources** (asyncpg :class:`~asyncpg.Pool`,
  :class:`packages.bus.NatsClient`, :class:`packages.market.BinanceWsClient`,
  :class:`packages.market.SubscriptionManager`,
  :class:`packages.market.OhlcPipeline`) live in the lifespan and
  attach to ``app.state`` inside the ``async with`` block. Teardown
  closes them in reverse order; ordering is load-bearing (see below).

Wired surface:

1. ``GET /health`` / ``GET /ready`` (T-100, :mod:`.health` router).
   ``/ready`` checks bus + db + ws; reason precedence bus → db → ws.
2. ``GET /metrics`` (T-100) — Prometheus ASGI mount with default
   process / platform / GC collectors. No service-specific metrics in
   T-100 per §0.8 (no concrete consumer yet); add when a real metric
   surfaces (likely with feature-engine T-110 or alerting F5).

Lifespan order (load-bearing):

1. ``pool = await create_pool(...)`` — DSN scheme-validated up front.
2. ``bus = NatsClient(...); await bus.connect()`` — JetStream context
   acquired; subscriptions/publishes ready.
3. ``ws = BinanceWsClient(initial_streams=set(), handler=_route_frame, ...)``
   — handler is a forwarding closure (see "Dispatch handler binding"
   below). Empty initial set; SubscriptionManager will populate via
   ``add_stream`` calls.
4. ``manager = SubscriptionManager(ws=ws, ...)`` — refcount facade
   per H-014 (T-102).
5. ``dispatch_holder.append(manager)`` — completes the forwarding
   closure binding so ``_route_frame`` resolves dispatch correctly.
6. ``pipeline = OhlcPipeline(subscription_mgr=manager, pool=pool,
   bus=bus, ...)`` — T-104b consumer.
7. Attach pool / bus / ws / manager / pipeline to ``app.state``.
8. ``ws_task = asyncio.create_task(ws.run(), name="binance_ws_run")``
   — long-lived loop; ``ws.close()`` triggers exit.
9. ``await pipeline.start(settings.symbols)`` — spawns one consumer
   task per symbol (each subscribes via ``manager.subscribe(symbol)``,
   which calls ``ws.add_stream`` for each kind).

   **Race note (steps 8 → 9):** ``pipeline.start()`` may run before
   ``ws.run()`` has reached ``CONNECTED`` (the WS connect happens
   inside the loop body, asynchronously after task creation). Per
   T-101b contract, ``ws.add_stream()`` is safe in any state — when
   disconnected, it just mutates the internal stream set; the next
   ``_reconnect_loop`` iteration picks up the new streams via the
   initial-subscribe step on connect. So no ``ws.wait_connected()``
   is needed; the optimistic ordering is correct by design.

Shutdown order (reverse of startup; also load-bearing):

10. ``await pipeline.stop()`` — cancels per-symbol tasks, which exit
    via :class:`SubscriptionManager` ``__aexit__`` and call
    ``ws.remove_stream`` UNSUBSCRIBE frames. **Must run before**
    ``ws.close()`` so the UNSUBSCRIBE frames land while the WS is
    still open.
11. ``await ws.close()`` then ``await ws_task`` with a 5 s timeout.
    Loop exits on close; task drains. Timeout fallback cancels the
    task — should never fire under normal shutdown.
12. ``await bus.close()`` — drains tracked subscriptions, then closes
    the NATS connection.
13. ``await pool.close()`` — releases asyncpg connections.

Dispatch handler binding (the holder pattern):

:class:`BinanceWsClient` requires its frame ``handler`` at construction
time (T-101b API). :class:`SubscriptionManager` requires the ``ws``
instance at construction time (T-102 API). The actual handler we want
to bind is ``manager.dispatch``, but ``manager`` cannot exist before
``ws`` does. We resolve the cycle with a single-element list closed
over by a forwarding closure: ``ws`` is built with the closure as its
handler, then ``manager`` is built referencing ``ws``, then the
manager is appended to the holder so the closure resolves to it.

Between ws construction (step 3) and the holder append (step 5),
there is a microsecond window where ``_route_frame`` will fire and
find the holder empty. In practice no frames arrive in that window
(``ws.run()`` hasn't been started yet — the receive loop is created
at step 8), so the warning log is purely defensive. A future reader
should NOT add a lock or ``manager_ready`` event to "tighten" the
binding — the transience is intentional, fully bounded by the
synchronous lifespan ordering, and the warning log surfaces any
future regression that breaks the ordering invariant.

Configuration deviation note (§6.7 procedural, no ADR):

Brief §9.2 line 1454 specifies the active-symbol set comes from a
``bots`` JOIN ``bot_configs`` query. F1 ships before F3's bot
registry populates those tables, so this skeleton uses the
``MARKET_DATA_SYMBOLS`` env-var stopgap (parsed via
:attr:`Settings.symbols`). Empty default → empty list →
:meth:`OhlcPipeline.start([])` is a no-op and the service stays
healthy + ready until the env var is populated. F1+ entry queued to
swap to the spec'd query when F3 lands; the swap is mechanical
because :class:`OhlcPipeline` accepts the symbol list at start.

No backfill in T-100. T-105 (queued, blocked by T-104b — now resolved)
ships REST ``/api/v3/klines`` backfill on startup + reconnect resync.
T-100 starts the pipeline cold; the first closed-bucket frame arrives
at the next minute boundary (≤60 s after WS connect).
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

from packages.bus import NatsClient
from packages.db import create_pool
from packages.market import (
    BinanceWsClient,
    OhlcPipeline,
    SubscriptionManager,
)
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


_WS_TASK_SHUTDOWN_TIMEOUT_SECONDS = 5.0


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` for market-data-svc.

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

    # Single system-stream logger. market-data-svc emits no trading
    # events in T-100 (the pipeline's persist + publish run from inside
    # OhlcPipeline which carries its own logger); a trading-stream
    # logger gets added when a service-level trading event surfaces.
    logger = get_logger(settings.service_name, "system")

    # Prometheus registry with default collectors only. Service
    # counters/histograms are added when concrete consumers surface
    # (no service metrics in T-100 per §0.8). The registry is
    # referenced again only by the /metrics ASGI mount below; not
    # exposed on app.state (no handler reads it).
    registry = make_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring."""
        # Holder for the forwarding-closure dispatch handler. Local
        # to the lifespan scope (not module-level) per §N6 — captured
        # by `_route_frame` via closure.
        dispatch_holder: list[SubscriptionManager] = []

        async def _route_frame(frame: dict[str, Any]) -> None:
            """Forward a Binance frame to SubscriptionManager.dispatch.

            Empty holder means the manager hasn't been appended yet —
            should only occur in the microsecond window between
            ``ws`` construction and the holder append. Logged at
            warning so a future regression that breaks the ordering
            invariant surfaces immediately.
            """
            if not dispatch_holder:
                logger.warning(
                    "market_data_dispatch_pre_bind",
                    frame_keys=list(frame),
                )
                return
            await dispatch_holder[0].dispatch(frame)

        pool = await create_pool(
            settings.database_url,
            application_name=settings.service_name,
        )
        bus = NatsClient(
            servers=[settings.nats_url],
            name=settings.service_name,
            logger=logger,
        )
        await bus.connect()

        ws = BinanceWsClient(
            initial_streams=set(),
            handler=_route_frame,
            logger=logger,
        )
        manager = SubscriptionManager(ws=ws, logger=logger)
        dispatch_holder.append(manager)
        pipeline = OhlcPipeline(
            subscription_mgr=manager,
            pool=pool,
            bus=bus,
            logger=logger,
        )

        app.state.pool = pool
        app.state.bus = bus
        app.state.ws = ws
        app.state.subscription_mgr = manager
        app.state.pipeline = pipeline

        ws_task = asyncio.create_task(ws.run(), name="binance_ws_run")
        await pipeline.start(settings.symbols)

        logger.info(
            "service_started",
            http_port=settings.http_port,
            symbols=settings.symbols,
        )
        try:
            yield
        finally:
            await pipeline.stop()
            await ws.close()
            try:
                async with asyncio.timeout(_WS_TASK_SHUTDOWN_TIMEOUT_SECONDS):
                    await ws_task
            except TimeoutError:
                logger.warning(
                    "ws_task_shutdown_timeout",
                    timeout_seconds=_WS_TASK_SHUTDOWN_TIMEOUT_SECONDS,
                )
                ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ws_task
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
