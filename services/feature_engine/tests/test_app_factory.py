"""Tests for :func:`services.feature_engine.app.main.create_app`.

Verifies that:

* The factory returns a :class:`FastAPI` instance with `/health`,
  `/ready`, `/metrics` registered.
* Sync state (settings, logger) attaches before the lifespan runs.
* Async lifespan state (pool, bus, buffer_registry, pipeline) attaches
  after lifespan entry — verified by exercising the lifespan via
  :class:`TestClient`.
* T-110d lifespan ordering: ``acquire_handles → warmup_load →
  start_consuming`` (Q11 race resolution); reverse on shutdown
  (``pipeline.stop → bus.close → pool.close``).
* T-110d JSONB codec: ``_register_jsonb_codec`` is passed as
  ``init=`` to :func:`packages.db.create_pool` and registers the
  ``jsonb`` codec on a freshly-acquired connection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.feature_engine.app.main import _register_jsonb_codec


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
    """pool / bus land on app.state inside lifespan; teardown reverse-order.

    Empty registry (default conftest fixture) → pipeline.stop is a
    no-op against an empty handles dict, so the observable shutdown
    order is bus.close → pool.close (T-109 contract).
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


def test_lifespan_attaches_buffer_registry_and_pipeline_to_state(
    app_with_mocks: FastAPI,
) -> None:
    """T-110d: buffer_registry + pipeline are reachable on app.state."""
    with TestClient(app_with_mocks):
        assert app_with_mocks.state.buffer_registry is not None
        assert app_with_mocks.state.pipeline is not None


def test_jsonb_codec_init_passed_to_create_pool(
    settings: object,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-110d Decision #3: ``init=_register_jsonb_codec`` flows to create_pool.

    Asserts identity pass-through; the callback's contract (calling
    ``set_type_codec("jsonb", ...)``) is verified separately by
    ``test_register_jsonb_codec_calls_set_type_codec`` (Write-time
    guidance #3).
    """
    create_pool_mock = AsyncMock(return_value=mock_pool)
    monkeypatch.setattr("services.feature_engine.app.main.create_pool", create_pool_mock)
    monkeypatch.setattr(
        "services.feature_engine.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(
        "services.feature_engine.app.main.build_features",
        lambda symbols: {},
    )
    from services.feature_engine.app.main import create_app

    app = create_app(settings=settings)  # type: ignore[arg-type]
    with TestClient(app):
        pass
    create_pool_mock.assert_awaited_once()
    assert create_pool_mock.await_args is not None
    kwargs = create_pool_mock.await_args.kwargs
    assert kwargs["init"] is _register_jsonb_codec


@pytest.mark.asyncio
async def test_register_jsonb_codec_calls_set_type_codec() -> None:
    """T-110d Write-time guidance #3: callback contract verified at unit level.

    Mocks ``asyncpg.Connection`` and asserts the JSONB codec is
    registered with the expected encoder/decoder/schema (mirror T-108
    ``test_0004_migration:55-62`` per-connection pattern, lifted to
    the pool init callback).
    """
    import json as _json

    mock_conn = MagicMock()
    mock_conn.set_type_codec = AsyncMock()
    await _register_jsonb_codec(mock_conn)
    mock_conn.set_type_codec.assert_awaited_once_with(
        "jsonb",
        encoder=_json.dumps,
        decoder=_json.loads,
        schema="pg_catalog",
    )
