"""Tests for Prometheus registry factory and /metrics ASGI wiring."""

from __future__ import annotations

from typing import Any

from prometheus_client import CollectorRegistry, Counter

from packages.observability.metrics import make_metrics_asgi_app, make_registry


def test_make_registry_returns_collector_registry() -> None:
    registry = make_registry()
    assert isinstance(registry, CollectorRegistry)


def test_make_registry_returns_fresh_instance_per_call() -> None:
    assert make_registry() is not make_registry()


def test_make_registry_preregisters_default_collectors() -> None:
    registry = make_registry()
    sample_names = {s.name for m in registry.collect() for s in m.samples}
    # ProcessCollector exposes process_* samples.
    assert any(name.startswith("process_") for name in sample_names)
    # PlatformCollector exposes python_info.
    assert "python_info" in sample_names


async def test_make_metrics_asgi_app_serves_counter_samples() -> None:
    registry = make_registry()
    counter = Counter(
        "scalper_test_events_total",
        "Test counter",
        ["label"],
        registry=registry,
    )
    counter.labels(label="a").inc(3)

    app = make_metrics_asgi_app(registry)

    received_started = False
    body_chunks: list[bytes] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal received_started
        if message["type"] == "http.response.start":
            received_started = True
            assert message["status"] == 200
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/metrics",
        "query_string": b"",
        "headers": [],
    }
    await app(scope, receive, send)

    assert received_started
    text = b"".join(body_chunks).decode("utf-8")
    assert 'scalper_test_events_total{label="a"} 3.0' in text


def test_make_metrics_asgi_app_returns_callable() -> None:
    registry = make_registry()
    app = make_metrics_asgi_app(registry)
    assert callable(app)
