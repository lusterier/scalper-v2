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

from prometheus_client import Counter, Gauge

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
    signals_skipped_sizing: Counter


def build_execution_metrics(registry: CollectorRegistry) -> Metrics:
    """Declare the §15.3 execution-service metrics on ``registry``.

    * T-531 ``virtual_balance{bot_id}`` — per-bot account equity snapshot
      (set to ``AccountBalance.total_equity`` by the equity-snapshot tick).
    * T-527b2b ``signals_skipped_sizing_total{bot_id,reason}`` — §B.1 sizing
      skipped a signal at the placement seam (``reason`` ∈ ``fetch_failed`` /
      ``compute_error`` / ``sub_lowest_tier``; mirror the strategy-engine
      ``signals_blocked_*`` T-526/T-524 silent-skip counter pattern).
    """
    return Metrics(
        virtual_balance=Gauge(
            "virtual_balance",
            "Per-bot account equity snapshot — total_equity (§15.3; T-531).",
            labelnames=("bot_id",),
            registry=registry,
        ),
        signals_skipped_sizing=Counter(
            "signals_skipped_sizing_total",
            "§B.1 sizing skipped a signal at the placement seam, by reason "
            "(fetch_failed / compute_error / sub_lowest_tier) (T-527b2b).",
            labelnames=("bot_id", "reason"),
            registry=registry,
        ),
    )
