"""FastAPI dependency providers for execution-service (§N6).

Providers read from :attr:`fastapi.Request.app.state`, where the
lifespan in :mod:`services.execution.app.main` attaches the asyncpg
pool and NATS client. Mirrors the feature-engine DI shape (T-109),
itself a mirror of market-data-svc (T-100) — single composition root,
no module globals.

Starlette's ``app.state`` exposes attributes as ``Any``; each provider
narrows to the expected type via :func:`typing.cast`. Runtime behaviour
is unchanged — the cast is purely for mypy ``warn_return_any``
compliance.

T-214 ships 4 providers: ``get_pool``, ``get_bus``, ``get_settings``,
``get_logger_dep``. Execution-specific providers will be added by
their owner tasks per §0.8: T-215 adds ``get_adapter_pool`` (for the
``dict[BotId, ExchangeClient]`` adapter pool); T-218 may add a
dispatcher provider; T-220 may add a scheduler provider.
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
    from packages.core import BotId
    from packages.exchange.protocols import ExchangeClient

    from .config import Settings

__all__ = [
    "get_adapter_pool",
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


def get_adapter_pool(request: Request) -> dict[BotId, ExchangeClient]:
    """Return ``app.state.adapters`` — per-bot ExchangeClient pool (T-215)."""
    return cast("dict[BotId, ExchangeClient]", request.app.state.adapters)
