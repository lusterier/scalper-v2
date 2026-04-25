"""FastAPI dependency providers for market-data-svc (§N6).

Providers read from :attr:`fastapi.Request.app.state`, where the
lifespan in :mod:`services.market_data.app.main` attaches the asyncpg
pool, NATS client, BinanceWsClient, SubscriptionManager, and
OhlcPipeline. Mirrors the signal-gateway DI shape (T-015a) — single
composition root, no module globals.

Starlette's ``app.state`` exposes attributes as ``Any``; each provider
narrows to the expected type via :func:`typing.cast`. Runtime behaviour
is unchanged — the cast is purely for mypy ``warn_return_any``
compliance.
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
    from packages.market import BinanceWsClient, OhlcPipeline, SubscriptionManager

    from .config import Settings

__all__ = [
    "get_bus",
    "get_logger_dep",
    "get_pipeline",
    "get_pool",
    "get_settings",
    "get_subscription_mgr",
    "get_ws",
]


def get_pool(request: Request) -> asyncpg.Pool:
    """Return the asyncpg pool attached to the app in lifespan."""
    return cast("asyncpg.Pool", request.app.state.pool)


def get_bus(request: Request) -> NatsClient:
    """Return the :class:`packages.bus.NatsClient` attached in lifespan."""
    return cast("NatsClient", request.app.state.bus)


def get_ws(request: Request) -> BinanceWsClient:
    """Return the :class:`packages.market.BinanceWsClient` attached in lifespan."""
    return cast("BinanceWsClient", request.app.state.ws)


def get_subscription_mgr(request: Request) -> SubscriptionManager:
    """Return the :class:`packages.market.SubscriptionManager` attached in lifespan."""
    return cast("SubscriptionManager", request.app.state.subscription_mgr)


def get_pipeline(request: Request) -> OhlcPipeline:
    """Return the :class:`packages.market.OhlcPipeline` attached in lifespan."""
    return cast("OhlcPipeline", request.app.state.pipeline)


def get_settings(request: Request) -> Settings:
    """Return the :class:`Settings` instance built in :func:`create_app`."""
    return cast("Settings", request.app.state.settings)


def get_logger_dep(request: Request) -> BoundLogger:
    """Return the system-stream :class:`BoundLogger` attached in lifespan."""
    return cast("BoundLogger", request.app.state.logger)
