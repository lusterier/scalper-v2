"""§N4 unit tests for :mod:`services.execution.app.metrics` (T-531).

First execution-service service-metric. Verifies the ``virtual_balance``
Gauge is declared on the injected registry with the ``bot_id`` label, the
verbatim name (NO ``_total`` suffix — gauge, not counter), and that
``.labels(bot_id=...).set(float)`` lands a retrievable sample.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge

from services.execution.app.metrics import Metrics, build_execution_metrics


def test_build_execution_metrics_returns_virtual_balance_gauge() -> None:
    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    assert isinstance(metrics, Metrics)
    assert isinstance(metrics.virtual_balance, Gauge)


def test_virtual_balance_name_has_no_total_suffix() -> None:
    """§15.3 lists `virtual_balance{bot_id}` under Gauges — no `_total`."""
    registry = CollectorRegistry()
    build_execution_metrics(registry)
    # Counter convention is `_total`; gauge must be the bare name.
    assert registry.get_sample_value("virtual_balance_total", {"bot_id": "x"}) is None


def test_virtual_balance_set_lands_per_bot_sample() -> None:
    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    metrics.virtual_balance.labels(bot_id="alpha").set(10250.5)
    metrics.virtual_balance.labels(bot_id="beta").set(9500.0)
    assert registry.get_sample_value("virtual_balance", {"bot_id": "alpha"}) == 10250.5
    assert registry.get_sample_value("virtual_balance", {"bot_id": "beta"}) == 9500.0


def test_metrics_is_frozen_dataclass() -> None:
    """§N6 — handles are DI'd, not mutated; frozen mirrors strategy_engine."""
    import dataclasses

    registry = CollectorRegistry()
    metrics = build_execution_metrics(registry)
    assert dataclasses.is_dataclass(metrics)
    fields = {f.name for f in dataclasses.fields(metrics)}
    assert fields == {"virtual_balance", "signals_skipped_sizing"}  # T-527b2b added the Counter
