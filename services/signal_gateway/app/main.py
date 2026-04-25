"""FastAPI app factory + lifespan for signal-gateway (§5.14, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each call
returns a fresh :class:`fastapi.FastAPI` instance.

Composition split (T-015b2a):

* **Synchronous primitives** are instantiated in the ``create_app`` body
  (Settings, two structlog loggers, Prometheus registry, :class:`Metrics`,
  T-015b1 :class:`RateLimiter`, :class:`DedupRing`) and attached to
  ``app.state`` immediately so dependency providers in :mod:`.deps` see
  them before lifespan runs (relevant for in-process unit tests that
  hit endpoints without lifespan startup).

* **Asynchronous resources** (asyncpg pool, :class:`packages.bus.NatsClient`,
  :class:`SymbolMapCache` — wraps the pool) live in the lifespan and
  attach to ``app.state`` inside the ``async with`` block. Teardown
  closes them in reverse order.

Wired surface:

1. ``GET /health`` / ``GET /ready`` (T-015a, :mod:`.health` router).
2. ``GET /metrics`` (T-015a) — Prometheus ASGI mount.
3. :class:`.middleware.RateLimitMiddleware` registered before the
   trace-bind HTTP middleware so the latter ends up outermost (Starlette
   prepends; last registered = outermost). Verified by
   ``test_trace_middleware_runs_before_rate_limit_middleware`` in
   ``tests/test_app_factory.py`` — if that test ever flips, swap the
   two registrations here.
4. ``POST /webhook`` is wired via :mod:`.webhook` (T-015b2b).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from packages.bus import NatsClient
from packages.core import TraceId
from packages.db import create_pool
from packages.observability import (
    add_redacted_keys,
    configure,
    get_logger,
    make_metrics_asgi_app,
    new_trace_id,
    trace_scope,
)

from .config import Settings
from .dedup import DedupRing
from .health import router as health_router
from .metrics import build_registry, build_signal_gateway_metrics
from .middleware import RateLimitMiddleware
from .rate_limit import RateLimiter
from .symbol_map import SymbolMapCache
from .webhook import router as webhook_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response

__all__ = ["create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` for signal-gateway.

    ``settings`` is injected for tests; in production it defaults to
    :class:`Settings` sourced from the process environment. A failure to
    validate env at this call site prevents uvicorn from ever binding the
    port (§5.11 fail-fast).
    """
    if settings is None:
        # Settings() reads env via pydantic-settings; mypy has no plugin
        # for env-sourcing, so required fields look "missing".
        settings = Settings()  # type: ignore[call-arg]

    # Observability bootstrap (T-015a) — happens before logger acquisition.
    configure(level=settings.log_level)
    add_redacted_keys("signal_gateway_hmac_secret", "x_signature")

    # Two stream-distinct loggers (§15.1). T-015b2 webhook handler emits
    # signal_* events to trading and webhook_error to system. Local var
    # name ``logger`` matches T-015a — kept verbatim per §0.8.
    logger = get_logger(settings.service_name, "system")
    trading_logger = get_logger(settings.service_name, "trading")

    # Prometheus registry + service metrics on the same registry. Single
    # source — registry is referenced again only by the /metrics mount
    # below; not exposed on app.state (handlers don't need it).
    registry = build_registry()
    metrics = build_signal_gateway_metrics(registry)

    # T-015b1 sync primitives. Defaults (window=60s/limit=20, ttl=10s)
    # match §16.3 / §9.1 step 4.
    rate_limiter = RateLimiter()
    dedup = DedupRing()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring for split."""
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
        symbol_cache = SymbolMapCache(pool)

        app.state.pool = pool
        app.state.bus = bus
        app.state.symbol_cache = symbol_cache

        logger.info("service_started", http_port=settings.http_port)
        try:
            yield
        finally:
            await bus.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    # Sync state attach happens here (not in lifespan) so deps.py
    # providers see typed primitives immediately after create_app()
    # returns. This is defensive against tests that hit endpoints
    # outside a TestClient context and never enter the lifespan.
    app.state.settings = settings
    app.state.logger = logger
    app.state.trading_logger = trading_logger
    app.state.metrics = metrics
    app.state.rate_limiter = rate_limiter
    app.state.dedup = dedup

    # Middleware registration. Starlette prepends to ``user_middleware``
    # so the LAST registered ends up outermost (runs FIRST on inbound).
    # Required wire order per docs/modules/signal_gateway.md "Pipeline
    # wire order":  trace_scope (outermost) → RateLimitMiddleware → handler.
    # Therefore: RateLimitMiddleware FIRST, trace_scope SECOND. Verified
    # by tests/test_app_factory.py (functional test exercises a 429
    # response and asserts X-Request-ID is present, proving trace_scope
    # ran on the response — only possible if it is outermost).
    app.add_middleware(
        RateLimitMiddleware,
        limiter=rate_limiter,
        metrics=metrics,
        logger=trading_logger,
    )

    @app.middleware("http")
    async def bind_trace(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Bind a structlog trace_id for the request lifetime (§15.2)."""
        incoming = request.headers.get("X-Request-ID")
        trace_id = TraceId(incoming) if incoming else new_trace_id()
        with trace_scope(trace_id=trace_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = trace_id
        return response

    app.include_router(health_router)
    app.include_router(webhook_router)
    app.mount("/metrics", make_metrics_asgi_app(registry))
    return app
