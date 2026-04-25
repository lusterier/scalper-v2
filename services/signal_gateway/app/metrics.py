"""Prometheus registry factory + service metrics for signal-gateway (§15.3).

T-015a shipped :func:`build_registry` with the default ``ProcessCollector``
/ ``PlatformCollector`` / ``GCCollector`` baseline. T-015b2 adds
:func:`build_signal_gateway_metrics`, which declares the §15.3 service
counters (``signals_received_total``, ``signals_validated_total``,
``errors_total``) plus the ``webhook_processing_seconds`` histogram on
the same registry and returns a :class:`Metrics` dataclass with the
handles. Split into two functions so T-015a's public API is unchanged;
the composition root in :mod:`services.signal_gateway.app.main` calls
both in lifespan and attaches the registry + metrics separately onto
``app.state``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prometheus_client import Counter, Histogram

from packages.observability import make_registry

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry

__all__ = ["Metrics", "build_registry", "build_signal_gateway_metrics"]


def build_registry() -> CollectorRegistry:
    """Return a fresh Prometheus registry for the service.

    Thin wrapper over :func:`packages.observability.make_registry` so
    T-015b can register signal-gateway counters/histograms here without
    editing the composition root in ``app/main.py``. Instantiated once per
    :func:`create_app` call; per-service isolation per §15.3.
    """
    return make_registry()


@dataclass(frozen=True)
class Metrics:
    """Service-level Prometheus handles (§15.3).

    Instantiated once per :func:`create_app` call by
    :func:`build_signal_gateway_metrics`; attached to ``app.state.metrics``
    in lifespan. The T-015b2 ``/webhook`` handler reads it via the
    ``get_metrics`` FastAPI dependency in ``app/deps.py``.

    Increment matrix — see ``docs/modules/signal_gateway.md`` "Pipeline
    wire order" section for the per-stage Counter/Histogram fire points.
    ``signals_validated_total{status}`` mirrors
    ``signals.ingestion_status`` (three values: ``validated``,
    ``duplicate``, ``invalid``); rejects without a DB row (invalid JSON,
    missing ``idempotency_key``) are tracked via ``errors_total``
    instead. ``error_class="validation_unkeyed"`` fires **only** when
    ``idempotency_key`` extraction failed (no key → can't identify
    row → can't insert); Pydantic-fail-with-key lands in
    ``signals_validated{status="invalid"}`` alongside a DB row, not
    here.
    """

    signals_received: Counter
    signals_validated: Counter
    errors: Counter
    webhook_processing_seconds: Histogram


def build_signal_gateway_metrics(registry: CollectorRegistry) -> Metrics:
    """Declare the §15.3 signal-gateway metrics on ``registry`` and return handles.

    Kept separate from :func:`build_registry` per §0.8: T-015a's
    registry-factory public API stays untouched; this call adds service
    counters + the request histogram on top. Instantiated once per
    :func:`create_app` call, aligned with ``build_registry`` — calling
    both functions on the same registry twice within one process would
    raise a duplicate-timeseries registration error from
    ``prometheus_client``.
    """
    return Metrics(
        signals_received=Counter(
            "signals_received_total",
            "Well-formed webhooks received (post HMAC + rate-limit + Pydantic validate).",
            labelnames=("source",),
            registry=registry,
        ),
        signals_validated=Counter(
            "signals_validated_total",
            "§9.1 validation outcomes; mirrors signals.ingestion_status enum.",
            labelnames=("status",),
            registry=registry,
        ),
        errors=Counter(
            "errors_total",
            "Service errors by class (§15.3).",
            labelnames=("service", "error_class"),
            registry=registry,
        ),
        webhook_processing_seconds=Histogram(
            "webhook_processing_seconds",
            "Wall time from handler entry to response.",
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
            registry=registry,
        ),
    )
