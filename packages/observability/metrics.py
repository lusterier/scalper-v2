"""Prometheus metrics bootstrap (§15.3).

Per-service pattern: create a fresh ``CollectorRegistry`` at startup,
register your service-specific counters/histograms/gauges against it,
then mount the ASGI app returned by :func:`make_metrics_asgi_app` under
``/metrics`` on the service's FastAPI instance. Standard metric
definitions from §15.3 live with their owning service (signal-gateway,
execution, etc.), not here — this package ships wiring only.

Avoiding the global ``prometheus_client.REGISTRY`` singleton keeps each
service isolated from test-time cross-contamination and lets pytest
instantiate a clean registry per fixture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prometheus_client import (
    CollectorRegistry,
    GCCollector,
    PlatformCollector,
    ProcessCollector,
    make_asgi_app,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["make_metrics_asgi_app", "make_registry"]


def make_registry() -> CollectorRegistry:
    """Return a fresh registry with process/platform/GC collectors pre-registered.

    The three default collectors are what ``prometheus_client`` attaches
    to its global ``REGISTRY`` implicitly; registering them here matches
    that behavior for per-service registries.
    """
    registry = CollectorRegistry(auto_describe=False)
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    GCCollector(registry=registry)
    return registry


def make_metrics_asgi_app(registry: CollectorRegistry) -> Callable[..., Any]:
    """Return an ASGI app serving the Prometheus text-exposition format.

    Mount on a FastAPI service with
    ``app.mount("/metrics", make_metrics_asgi_app(registry))``.
    """
    return make_asgi_app(registry=registry)
