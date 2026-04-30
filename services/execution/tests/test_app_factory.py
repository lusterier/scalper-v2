"""Tests for :func:`services.execution.app.main.create_app`.

Verifies that:

* The factory returns a :class:`FastAPI` instance with `/health`,
  `/ready`, `/metrics` registered.
* Sync state (settings, logger) attaches before the lifespan runs.
* Async lifespan state (pool, bus) attaches after lifespan entry —
  verified by exercising the lifespan via :class:`TestClient`.
* T-214 lifespan ordering: pool → bus → state attach; reverse on
  shutdown (``bus.close → pool.close``). Order matters per T-200 Q2
  publish-after-persist contract — the pool must outlive the bus so
  any in-flight publish that touches the pool finishes against an
  open pool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from fastapi import FastAPI


def test_create_app_returns_fastapi_instance(app_with_mocks: FastAPI) -> None:
    from fastapi import FastAPI as _FastAPI

    assert isinstance(app_with_mocks, _FastAPI)


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
    """`/metrics` returns 200 with default Prometheus collectors in the body."""
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
    """pool / bus land on app.state inside lifespan; teardown reverse-order.

    Order ``bus.close → pool.close`` is load-bearing for the future
    T-216 publish-after-persist flow (T-200 Q2): pool stays open until
    the bus has fully drained.
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


def test_lifespan_attaches_adapters_rate_limiter_and_task_lists(
    app_with_mocks: FastAPI,
    mock_rate_limiter: MagicMock,
    mock_adapter_pool_result: MagicMock,
) -> None:
    """T-215: app.state carries adapters / rate_limiter / ws_tasks / paper_consumer_tasks."""
    with TestClient(app_with_mocks):
        assert app_with_mocks.state.rate_limiter is mock_rate_limiter
        assert app_with_mocks.state.adapters is mock_adapter_pool_result.adapters
        assert app_with_mocks.state.ws_tasks is mock_adapter_pool_result.ws_tasks
        assert (
            app_with_mocks.state.paper_consumer_tasks
            is mock_adapter_pool_result.paper_consumer_tasks
        )
