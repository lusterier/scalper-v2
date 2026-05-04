"""FastAPI dependency providers for alerting-svc (T-409, §N6).

Providers read from :attr:`fastapi.Request.app.state`, where the lifespan
in :mod:`services.alerting.app.main` attaches the NATS client + alerts
config + dedup tracker + telegram client + now_fn callable. Mirror of
analytics-api deps shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import Request  # noqa: TC002 — FastAPI inspects Request annotation at DI resolution

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from packages.bus import NatsClient

    from .config import AlertsConfig, Settings
    from .dedup import DedupTracker
    from .telegram import TelegramClient

__all__ = [
    "get_alerts_config",
    "get_bus",
    "get_dedup",
    "get_logger_dep",
    "get_now_fn",
    "get_settings",
    "get_telegram_client",
]


def get_bus(request: Request) -> NatsClient:
    """Return the :class:`packages.bus.NatsClient` attached in lifespan."""
    return cast("NatsClient", request.app.state.bus)


def get_settings(request: Request) -> Settings:
    """Return the :class:`Settings` instance built in :func:`create_app`."""
    return cast("Settings", request.app.state.settings)


def get_logger_dep(request: Request) -> object:
    """Return the system-stream BoundLogger attached in lifespan."""
    return request.app.state.logger


def get_alerts_config(request: Request) -> AlertsConfig:
    """Return the :class:`AlertsConfig` loaded at lifespan startup."""
    return cast("AlertsConfig", request.app.state.alerts_config)


def get_telegram_client(request: Request) -> TelegramClient:
    """Return the :class:`TelegramClient` instance attached in lifespan."""
    return cast("TelegramClient", request.app.state.telegram_client)


def get_dedup(request: Request) -> DedupTracker:
    """Return the :class:`DedupTracker` instance attached in lifespan."""
    return cast("DedupTracker", request.app.state.dedup)


def get_now_fn(request: Request) -> Callable[[], datetime]:
    """Return the ``now_fn`` callable attached in lifespan (testability hook)."""
    return cast("Callable[[], datetime]", request.app.state.now_fn)
