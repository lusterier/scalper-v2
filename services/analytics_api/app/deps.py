"""FastAPI dependency providers for analytics-api (§N6).

Providers read from :attr:`fastapi.Request.app.state`, where the
lifespan in :mod:`services.analytics_api.app.main` attaches the
asyncpg pool, NATS client, and (T-401b) `now_fn` callable. Mirrors
the strategy-engine DI shape (T-309), itself a mirror of execution-
service (T-214) — single composition root, no module globals.

Starlette's ``app.state`` exposes attributes as ``Any``; each provider
narrows to the expected type via :func:`typing.cast`. Runtime behaviour
is unchanged — the cast is purely for mypy ``warn_return_any``
compliance.

T-400 shipped 4 providers: ``get_pool``, ``get_bus``, ``get_settings``,
``get_logger_dep``. T-401b adds ``get_now_fn`` — first canonical
FastAPI deps ``now_fn`` provider in the repo (other services plumb it
as constructor argument; T-401b establishes the FastAPI deps pattern
for analytics-api endpoints needing audit-row timestamps). T-405+
reuse via import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

# FastAPI inspects Request parameter annotations at DI resolution time;
# the import is runtime-required for injection, not typing-only.
from fastapi import Request  # noqa: TC002

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient

    from .config import Settings

__all__ = [
    "get_bus",
    "get_logger_dep",
    "get_now_fn",
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


def get_now_fn(request: Request) -> Callable[[], datetime]:
    """Return the ``now_fn`` callable attached to ``app.state`` in lifespan.

    Tests monkey-patch ``client.app.state.now_fn`` to a fixed-time lambda
    for deterministic timestamps in audit_events writes (T-401b WG#7).
    """
    return cast("Callable[[], datetime]", request.app.state.now_fn)
