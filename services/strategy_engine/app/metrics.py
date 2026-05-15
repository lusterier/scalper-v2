"""Prometheus registry factory + service metrics for strategy-engine (§15.3).

Mirror :mod:`services.signal_gateway.app.metrics` pattern. T-526 ships the
first service counter: ``signals_blocked_cooldown_total{bot_id, reason}`` —
incremented by the consumer pre-scoring cooldown gate when a signal is
suppressed per :mod:`services.strategy_engine.app.cooldown_gate`.

Per BRIEF §15.3 the strategy-engine service has additional metrics planned
(``orders_placed_total``, ``signal_to_order_seconds`` histogram); those are
owned by execution-service / strategy-engine future tasks and not in T-526
scope. Adding them here means surfacing them with explicit task references.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from prometheus_client import Counter

from packages.observability import make_registry

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry

__all__ = ["Metrics", "build_registry", "build_strategy_engine_metrics"]


def build_registry() -> CollectorRegistry:
    """Return a fresh Prometheus registry for the service.

    Mirror :func:`services.signal_gateway.app.metrics.build_registry`. Thin
    wrapper over :func:`packages.observability.make_registry`.
    """
    return make_registry()


@dataclass(frozen=True)
class Metrics:
    """Service-level Prometheus handles (§15.3).

    Instantiated once per :func:`create_app` call. Attached to
    ``app.state.metrics``; consumer reads via factory kwarg.
    """

    signals_blocked_cooldown: Counter
    signals_blocked_caps: Counter
    signals_blocked_loss_limit: Counter
    signals_blocked_drawdown: Counter


def build_strategy_engine_metrics(registry: CollectorRegistry) -> Metrics:
    """Declare the §15.3 strategy-engine metrics on ``registry`` and return handles.

    * T-526 ``signals_blocked_cooldown_total{bot_id, reason}`` — incremented
      each time the pre-scoring cooldown gate blocks a signal.
    * T-524 ``signals_blocked_caps_total{bot_id, reason}`` — incremented each
      time the pre-scoring concurrent-trades caps gate blocks a signal
      (``max_open_trades_per_bot`` / ``max_open_trades_global``).
    * T-525a2 ``signals_blocked_loss_limit_total{bot_id, reason}`` —
      incremented each time the pre-scoring daily-loss kill-switch gate blocks
      a signal (``reason`` = the binding latch reason: ``daily_loss_limit`` on
      a fresh trip, or the pre-existing latch's reason — e.g. ``max_drawdown``).
    * T-525b ``signals_blocked_drawdown_total{bot_id, reason}`` — incremented
      each time the pre-scoring max-drawdown hard-stop gate blocks a signal
      (``reason`` = ``max_drawdown`` on a fresh trip, or the pre-existing
      latch's reason — reason-agnostic cross-block).
    """
    return Metrics(
        signals_blocked_cooldown=Counter(
            "signals_blocked_cooldown_total",
            "Signals suppressed by the strategy-engine pre-scoring cooldown gate (T-526).",
            labelnames=("bot_id", "reason"),
            registry=registry,
        ),
        signals_blocked_caps=Counter(
            "signals_blocked_caps_total",
            "Signals suppressed by the strategy-engine pre-scoring concurrent-caps gate (T-524).",
            labelnames=("bot_id", "reason"),
            registry=registry,
        ),
        signals_blocked_loss_limit=Counter(
            "signals_blocked_loss_limit_total",
            "Signals suppressed by the pre-scoring daily-loss kill-switch gate (T-525a2).",
            labelnames=("bot_id", "reason"),
            registry=registry,
        ),
        signals_blocked_drawdown=Counter(
            "signals_blocked_drawdown_total",
            "Signals suppressed by the pre-scoring max-drawdown hard-stop gate (T-525b).",
            labelnames=("bot_id", "reason"),
            registry=registry,
        ),
    )
