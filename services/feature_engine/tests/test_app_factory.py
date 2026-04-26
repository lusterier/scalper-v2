"""Tests for :func:`services.feature_engine.app.main.create_app`.

Verifies that:

* The factory returns a :class:`FastAPI` instance with `/health`,
  `/ready`, `/metrics` registered.
* Sync state (settings, logger) attaches before the lifespan runs
  (relevant for tests that hit endpoints without entering the
  TestClient lifespan context).
* Async lifespan state (pool, bus) attaches after lifespan entry —
  verified by exercising the lifespan via :class:`TestClient` and
  asserting the mocks are reachable on ``app.state``.
* Lifespan teardown closes resources in reverse order: bus first
  (drains in-flight publishes against the still-open pool), then
  pool (releases asyncpg connections).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from unittest.mock import MagicMock


def test_create_app_returns_fastapi_instance(app_with_mocks: FastAPI) -> None:
    assert isinstance(app_with_mocks, FastAPI)


def test_routes_registered(app_with_mocks: FastAPI) -> None:
    """`/health`, `/ready`, `/metrics` are all reachable."""
    paths = {route.path for route in app_with_mocks.routes}  # type: ignore[attr-defined]
    assert "/health" in paths
    assert "/ready" in paths
    # `/metrics` is mounted as a sub-app; appears as a Mount in routes.
    mounts = {
        route.path  # type: ignore[attr-defined]
        for route in app_with_mocks.routes
        if route.__class__.__name__ == "Mount"
    }
    assert "/metrics" in mounts


def test_metrics_endpoint_serves_default_collectors(client: TestClient) -> None:
    """`/metrics` returns 200 with default Prometheus collectors in the body.

    The exact content-type (``text/plain`` vs
    ``application/openmetrics-text``) is decided by
    ``prometheus_client.make_asgi_app``'s Accept-header negotiation —
    not a T-109 contract — so we assert on body content, not headers.
    Absence of ``python_info`` would mean the registry isn't wired
    through to the mounted ASGI app.
    """
    response = client.get("/metrics")
    assert response.status_code == 200
    assert b"python_info" in response.content


def test_sync_state_attached_before_lifespan(app_with_mocks: FastAPI) -> None:
    """Settings + logger land on app.state in create_app body, not in lifespan."""
    assert app_with_mocks.state.settings is not None
    assert app_with_mocks.state.logger is not None


def test_lifespan_attaches_pool_and_bus_and_closes_in_reverse_order(
    app_with_mocks: FastAPI,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
) -> None:
    """pool / bus land on app.state inside lifespan; teardown closes bus before pool.

    The TestClient context manager runs the lifespan startup on enter
    and teardown on exit. While inside the ``with`` block, both pool
    and bus must be reachable on ``app.state``. After exit, both
    ``close`` coroutines must have been awaited, with bus.close
    called before pool.close (reverse-order shutdown contract:
    bus drains in-flight publishes against the still-open pool).
    """
    call_order: list[str] = []
    mock_bus.close.side_effect = lambda: call_order.append("bus")
    mock_pool.close.side_effect = lambda: call_order.append("pool")

    with TestClient(app_with_mocks):
        assert app_with_mocks.state.pool is mock_pool
        assert app_with_mocks.state.bus is mock_bus

    mock_bus.close.assert_awaited_once()
    mock_pool.close.assert_awaited_once()
    assert call_order == ["bus", "pool"]
