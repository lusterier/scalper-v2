"""FastAPI app factory + lifespan for signal-gateway (§5.14, §N6, §15.2).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each call
returns a fresh :class:`fastapi.FastAPI` instance with:

1. A lifespan that manages the asyncpg pool + :class:`NatsClient`
   connection (composition root — nothing module-global).
2. An HTTP middleware binding a structlog ``trace_id`` for every request
   (§15.2; propagates the inbound ``X-Request-ID`` header or mints one).
3. The Prometheus ASGI sub-app mounted at ``/metrics``.
4. The :mod:`.health` router (``/health``, ``/ready``).
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
from .health import router as health_router
from .metrics import build_registry

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

    registry = build_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root: build pool + bus, attach to state, tear down."""
        configure(level=settings.log_level)
        add_redacted_keys("signal_gateway_hmac_secret", "x_signature")
        logger = get_logger(settings.service_name, "system")
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

        app.state.logger = logger
        app.state.pool = pool
        app.state.bus = bus

        logger.info("service_started", http_port=settings.http_port)
        try:
            yield
        finally:
            await bus.close()
            await pool.close()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

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
    app.mount("/metrics", make_metrics_asgi_app(registry))
    return app
