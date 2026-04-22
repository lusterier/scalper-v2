"""Tests for :func:`services.signal_gateway.app.main.create_app`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _route_paths(app: FastAPI) -> set[str]:
    """Collect every routed path on ``app`` (Routes and Mounts)."""
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
    return paths


def test_create_app_returns_fastapi(app_with_mocks: FastAPI) -> None:
    assert isinstance(app_with_mocks, FastAPI)


def test_app_exposes_health_route(app_with_mocks: FastAPI) -> None:
    assert "/health" in _route_paths(app_with_mocks)


def test_app_exposes_ready_route(app_with_mocks: FastAPI) -> None:
    assert "/ready" in _route_paths(app_with_mocks)


def test_app_mounts_metrics(app_with_mocks: FastAPI) -> None:
    """The Prometheus ASGI sub-app is attached at ``/metrics``."""
    assert "/metrics" in _route_paths(app_with_mocks)


def test_metrics_endpoint_returns_prometheus_text(client: TestClient) -> None:
    """``GET /metrics`` returns 200 + baseline collector output.

    ``python_info`` comes from :class:`prometheus_client.PlatformCollector`
    and is stable across prometheus_client versions. T-015b will add
    service counters; this assertion only verifies the scrape surface is
    live in T-015a, not the specific metric set.
    """
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "python_info" in response.text


def test_no_webhook_route_in_t_015a(app_with_mocks: FastAPI) -> None:
    """``/webhook`` is T-015b; must not be registered in the skeleton."""
    assert "/webhook" not in _route_paths(app_with_mocks)
