"""FastAPI dependency providers for signal-gateway (§N6).

Providers read from :attr:`fastapi.Request.app.state`, where the lifespan
in :mod:`services.signal_gateway.app.main` attaches the pool, bus client,
logger, settings, T-015b1 primitives (rate limiter, dedup ring, symbol-map
cache), and the T-015b2 :class:`Metrics` dataclass. This keeps the
composition root single-point: no module globals, nothing to import-time
instantiate.

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

    from .config import Settings
    from .dedup import DedupRing
    from .metrics import Metrics
    from .rate_limit import RateLimiter
    from .symbol_map import SymbolMapCache

__all__ = [
    "get_bus",
    "get_dedup",
    "get_logger_dep",
    "get_metrics",
    "get_pool",
    "get_rate_limiter",
    "get_settings",
    "get_symbol_map_cache",
    "get_trading_logger",
]


def get_pool(request: Request) -> asyncpg.Pool:
    """Return the asyncpg pool attached to the app in lifespan."""
    return cast("asyncpg.Pool", request.app.state.pool)


def get_bus(request: Request) -> NatsClient:
    """Return the :class:`packages.bus.NatsClient` attached in lifespan."""
    return cast("NatsClient", request.app.state.bus)


def get_logger_dep(request: Request) -> BoundLogger:
    """Return the system-stream :class:`BoundLogger` attached in lifespan.

    Used by the T-015b2 ``/webhook`` handler for ``webhook_error`` events
    (exception path, publish fail, DB fail). Trading-stream events go
    through :func:`get_trading_logger` instead.
    """
    return cast("BoundLogger", request.app.state.logger)


def get_settings(request: Request) -> Settings:
    """Return the :class:`Settings` instance built in :func:`create_app`."""
    return cast("Settings", request.app.state.settings)


def get_rate_limiter(request: Request) -> RateLimiter:
    """Return the shared :class:`RateLimiter` (T-015b1) attached in lifespan."""
    return cast("RateLimiter", request.app.state.rate_limiter)


def get_dedup(request: Request) -> DedupRing:
    """Return the shared :class:`DedupRing` (T-015b1) attached in lifespan."""
    return cast("DedupRing", request.app.state.dedup)


def get_symbol_map_cache(request: Request) -> SymbolMapCache:
    """Return the shared :class:`SymbolMapCache` (T-015b1) attached in lifespan."""
    return cast("SymbolMapCache", request.app.state.symbol_cache)


def get_metrics(request: Request) -> Metrics:
    """Return the service :class:`Metrics` handles attached in lifespan."""
    return cast("Metrics", request.app.state.metrics)


def get_trading_logger(request: Request) -> BoundLogger:
    """Return the trading-stream :class:`BoundLogger` attached in lifespan.

    Distinct from :func:`get_logger_dep` (system stream): per §15.1 the
    signal-gateway emits ``signal_received`` / ``signal_rejected`` /
    ``signal_validated`` to ``trading.log`` and ``webhook_error`` to
    ``system.log``. Handler picks per event.
    """
    return cast("BoundLogger", request.app.state.trading_logger)
