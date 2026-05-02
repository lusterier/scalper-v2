"""FastAPI dependency providers for strategy-engine (§N6).

Providers read from :attr:`fastapi.Request.app.state`, where the
lifespan in :mod:`services.strategy_engine.app.main` attaches the
asyncpg pool, NATS client, plugin registry, bot config, and feature
resolver. Mirrors the execution-service DI shape (T-214), itself a
mirror of feature-engine (T-109) — single composition root, no module
globals.

Starlette's ``app.state`` exposes attributes as ``Any``; each provider
narrows to the expected type via :func:`typing.cast`. Runtime behaviour
is unchanged — the cast is purely for mypy ``warn_return_any``
compliance.

T-309 ships 5 providers: ``get_pool``, ``get_bus``, ``get_settings``,
``get_logger_dep``, ``get_bot_config``. T-310 will reach into
``app.state.resolver`` directly from the consumer body (one call site,
not a request handler) — the resolver provider lands when (if) a
request handler ever needs it.
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
    from packages.scoring import BotConfig

    from .config import Settings

__all__ = [
    "get_bot_config",
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


def get_bot_config(request: Request) -> BotConfig:
    """Return the :class:`BotConfig` loaded at lifespan startup (§9.4:1546)."""
    return cast("BotConfig", request.app.state.bot_config)
