"""FastAPI dependency providers for signal-gateway (§N6).

Providers read from :attr:`fastapi.Request.app.state`, where the lifespan
in :mod:`services.signal_gateway.app.main` attaches the pool, bus client,
and logger. This keeps the composition root single-point: no module
globals, nothing to import-time instantiate.

Starlette's ``app.state`` exposes attributes as ``Any``; each provider
narrows to the expected type via :func:`typing.cast`. Runtime behaviour
is unchanged — the cast is purely for mypy ``warn_return_any`` compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

# FastAPI inspects Request parameter annotations at DI resolution time;
# the import is runtime-required for injection, not typing-only.
from fastapi import Request  # noqa: TC002

if TYPE_CHECKING:
    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient

__all__ = ["get_bus", "get_logger_dep", "get_pool"]


def get_pool(request: Request) -> asyncpg.Pool:
    """Return the asyncpg pool attached to the app in lifespan."""
    return cast("asyncpg.Pool", request.app.state.pool)


def get_bus(request: Request) -> NatsClient:
    """Return the :class:`packages.bus.NatsClient` attached in lifespan."""
    return cast("NatsClient", request.app.state.bus)


def get_logger_dep(request: Request) -> BoundLogger:
    """Return the structlog :class:`BoundLogger` attached in lifespan."""
    return cast("BoundLogger", request.app.state.logger)
