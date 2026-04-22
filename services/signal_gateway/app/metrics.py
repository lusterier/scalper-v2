"""Prometheus registry factory for signal-gateway (§15.3).

T-015a ships the default ``ProcessCollector`` / ``PlatformCollector`` /
``GCCollector`` baseline only — enough for the
``infra/prometheus/prometheus.yml`` scrape target to flip from DOWN to UP.
Service-level counters (``signals_received_total``,
``signals_validated_total``, ``errors_total``, ``webhook_processing_seconds``)
are declared alongside this factory in T-015b as one atomic change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from packages.observability import make_registry

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry

__all__ = ["build_registry"]


def build_registry() -> CollectorRegistry:
    """Return a fresh Prometheus registry for the service.

    Thin wrapper over :func:`packages.observability.make_registry` so
    T-015b can register signal-gateway counters/histograms here without
    editing the composition root in ``app/main.py``. Instantiated once per
    :func:`create_app` call; per-service isolation per §15.3.
    """
    return make_registry()
