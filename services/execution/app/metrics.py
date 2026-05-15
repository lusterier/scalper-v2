"""Prometheus service metrics for execution-service (§15.3).

T-531 ships the first execution-service service-metric: the
``virtual_balance{bot_id}`` Gauge (BRIEF §15.3:2161 — "promoted to mandatory
T-531"). Set by the equity-snapshot APScheduler tick
(:mod:`services.execution.app.equity_snapshot`) to the bot's
``AccountBalance.total_equity`` each tick.

Mirror :mod:`services.strategy_engine.app.metrics` (dataclass + builder)
EXCEPT no ``build_registry()``: execution-service already owns its registry
via :func:`packages.observability.make_registry` at ``main.py`` create_app
scope (mounted at ``/metrics``); this module only declares handles on it.

Gauge name is verbatim ``virtual_balance`` (NO ``_total`` suffix — that is
the counter convention; §15.3 lists ``virtual_balance{bot_id}`` under
Gauges). The gauge is monitoring-only; it is NOT read by sizing / P&L
(those consume ``AccountBalance`` ``Decimal`` directly).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prometheus_client import Gauge

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry

__all__ = ["Metrics", "build_execution_metrics"]


@dataclass(frozen=True)
class Metrics:
    """Service-level Prometheus handles (§15.3).

    Instantiated once per :func:`create_app` call (after the registry is
    created, before lifespan). Captured by the equity-snapshot job closure;
    DI per §N6 (no module-global metric handles).
    """

    virtual_balance: Gauge


def build_execution_metrics(registry: CollectorRegistry) -> Metrics:
    """Declare the §15.3 execution-service metrics on ``registry``.

    * T-531 ``virtual_balance{bot_id}`` — per-bot account equity snapshot
      (set to ``AccountBalance.total_equity`` by the equity-snapshot tick).
    """
    return Metrics(
        virtual_balance=Gauge(
            "virtual_balance",
            "Per-bot account equity snapshot — total_equity (§15.3; T-531).",
            labelnames=("bot_id",),
            registry=registry,
        ),
    )
