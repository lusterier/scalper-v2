"""FastAPI dependency providers for analytics-api (§N6).

Providers read from :attr:`fastapi.Request.app.state`, where the
lifespan in :mod:`services.analytics_api.app.main` attaches the
asyncpg pool and NATS client. Mirrors the strategy-engine DI shape
(T-309), itself a mirror of execution-service (T-214) — single
composition root, no module globals.

Starlette's ``app.state`` exposes attributes as ``Any``; each provider
narrows to the expected type via :func:`typing.cast`. Runtime behaviour
is unchanged — the cast is purely for mypy ``warn_return_any``
compliance.

T-400 ships 4 providers: ``get_pool``, ``get_bus``, ``get_settings``,
``get_logger_dep``. Future endpoint tasks (T-401..T-408) add their own
providers as request handlers need them (e.g. ``get_query_runner``,
``get_sse_broker``).
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

    from .config import Settings

__all__ = [
    "get_bus",
    "get_logger_dep",
    "get_pool",
    "get_settings",
]


def get_pool(request: Request) -> asyncpg.Pool:
    """Return the asyncpg pool attached to the app in lifespan."""
    return cast("asyncpg.Pool", request.app.state.pool)


def get_bus(request: Request) -> NatsClient:
    """Return the :class:`packages.bus.NatsClient` attached in lifespan."""
    return cast("NatsClient", request.app.state.bus)


def get_settings(request: Request) -> Settings:
    """Return the :class:`Settings` instance built in :func:`create_app`."""
    return cast("Settings", request.app.state.settings)


def get_logger_dep(request: Request) -> BoundLogger:
    """Return the system-stream :class:`BoundLogger` attached in lifespan."""
    return cast("BoundLogger", request.app.state.logger)
