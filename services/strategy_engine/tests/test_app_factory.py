"""Tests for :func:`services.strategy_engine.app.main.create_app`.

Verifies that:

* The factory returns a :class:`FastAPI` instance with `/health`,
  `/ready`, `/metrics` registered.
* Sync state (settings, logger) attaches before the lifespan runs.
* Async lifespan state (pool, bus, plugin_registry, bot_config,
  resolver) attaches after lifespan entry — verified by exercising
  the lifespan via :class:`TestClient`.
* T-309 lifespan ordering (WG#1+#2+#4):
  * plugin_registry MUST load BEFORE bot_config (kwarg dependency).
  * `load_bot_config(path: Path, ...)` receives a :class:`pathlib.Path`,
    not str.
  * Reverse shutdown: bus.close BEFORE pool.close (T-200 Q2 publish-
    after-persist contract — pool must outlive the bus so any in-flight
    publish that touches the pool finishes against an open pool).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from services.strategy_engine.app.main import create_app

if TYPE_CHECKING:
    import pytest
    from fastapi import FastAPI

    from services.strategy_engine.app.config import Settings


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


def test_lifespan_attaches_pool_bus_resolver_bot_config_plugin_registry(
    app_with_mocks: FastAPI,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_plugin_registry: dict[tuple[str, str], type],
    mock_bot_config: MagicMock,
) -> None:
    """All 5 async-lifespan keys land on app.state inside the `async with` block."""
    with TestClient(app_with_mocks):
        assert app_with_mocks.state.pool is mock_pool
        assert app_with_mocks.state.bus is mock_bus
        assert app_with_mocks.state.plugin_registry is mock_plugin_registry
        assert app_with_mocks.state.bot_config is mock_bot_config
        # FeatureResolver is constructed inside the lifespan with bus+pool+logger;
        # we don't compare identity, but verify presence + type.
        assert app_with_mocks.state.resolver is not None


def test_lifespan_loads_bot_config_using_settings_bot_id(
    settings: Settings,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_plugin_registry: dict[tuple[str, str], type],
    mock_bot_config: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#4 — `load_bot_config(path: Path, ...)` receives Path, not str.

    Path constructed as ``Path(settings.bot_config_dir) / f"{bot_id}.yaml"``.
    """
    captured_args: list[tuple[Path, dict[str, object]]] = []

    def _capture_load_bot_config(path: Path, **kwargs: object) -> MagicMock:
        captured_args.append((path, kwargs))
        return mock_bot_config

    monkeypatch.setattr(
        "services.strategy_engine.app.main.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.load_plugin_registry",
        MagicMock(return_value=mock_plugin_registry),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.load_bot_config",
        _capture_load_bot_config,
    )
    app = create_app(settings=settings)
    with TestClient(app):
        pass

    assert len(captured_args) == 1
    path_arg, kwargs_arg = captured_args[0]
    assert isinstance(path_arg, Path), f"expected Path, got {type(path_arg).__name__}"
    assert path_arg == Path(settings.bot_config_dir) / f"{settings.bot_id}.yaml"
    assert kwargs_arg["plugin_registry"] is mock_plugin_registry


def test_lifespan_loads_plugin_registry_first(
    settings: Settings,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_plugin_registry: dict[tuple[str, str], type],
    mock_bot_config: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#1 — plugin_registry MUST load BEFORE bot_config (kwarg dependency)."""
    call_log: list[str] = []

    def _capture_load_plugin_registry(_path: Path) -> dict[tuple[str, str], type]:
        call_log.append("plugin_registry")
        return mock_plugin_registry

    def _capture_load_bot_config(_path: Path, **_kwargs: object) -> MagicMock:
        call_log.append("bot_config")
        return mock_bot_config

    monkeypatch.setattr(
        "services.strategy_engine.app.main.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.load_plugin_registry",
        _capture_load_plugin_registry,
    )
    monkeypatch.setattr(
        "services.strategy_engine.app.main.load_bot_config",
        _capture_load_bot_config,
    )
    app = create_app(settings=settings)
    with TestClient(app):
        pass

    assert call_log == ["plugin_registry", "bot_config"]


def test_lifespan_closes_bus_before_pool(
    app_with_mocks: FastAPI,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
) -> None:
    """WG#2 — bus.close BEFORE pool.close per T-200 Q2 publish-after-persist.

    T-310 will publish OrderRequest post-`scoring_evaluations` INSERT;
    pool must outlive the bus so any in-flight publish referencing pool
    state finishes against an open pool.
    """
    call_order: list[str] = []
    mock_bus.close.side_effect = lambda: call_order.append("bus")
    mock_pool.close.side_effect = lambda: call_order.append("pool")

    with TestClient(app_with_mocks):
        pass

    mock_bus.close.assert_awaited_once()
    mock_pool.close.assert_awaited_once()
    assert call_order == ["bus", "pool"]
