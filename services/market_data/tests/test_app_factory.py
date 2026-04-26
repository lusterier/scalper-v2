"""Tests for :func:`services.market_data.app.main.create_app`.

Verifies that:

* The factory returns a :class:`FastAPI` instance with `/health`,
  `/ready`, `/metrics` registered.
* Sync state (settings, logger) attaches before the lifespan runs
  (relevant for tests that hit endpoints without entering the
  TestClient lifespan context).
* Async lifespan state (pool, bus, ws, subscription_mgr, pipeline)
  attaches after lifespan entry — verified by exercising the lifespan
  via :class:`TestClient` and asserting the mocks are reachable on
  ``app.state``.
* The lifespan calls :meth:`OhlcPipeline.start` with the parsed
  symbol list (T-100 wires `Settings.symbols` → `pipeline.start`).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest  # noqa: TC002  # pytest fixture resolution needs runtime type access
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.bus import ConnectionState as BusConnectionState
from packages.market import ConnectionState as WsConnectionState
from services.market_data.app.config import Settings
from services.market_data.app.main import create_app

# ---------------------------------------------------------------------------
# Helper: build a real app with the OhlcPipeline mocked so tests can capture
# pipeline.start() calls. Used by the symbol-passthrough tests below; the
# happy-path tests above use the conftest `app_with_mocks` fixture instead
# (which builds the *real* OhlcPipeline against the mocked sub_mgr/pool/bus).
# ---------------------------------------------------------------------------


def _build_app_with_pipeline_capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    symbols_env: str | None,
) -> tuple[FastAPI, MagicMock]:
    """Wire the lifespan with a mocked pipeline + return (app, pipeline_mock).

    ``symbols_env`` is the value to set for ``MARKET_DATA_SYMBOLS``;
    ``None`` deletes the env var (covers the default-empty case).
    Settings + asyncpg pool + NatsClient + BinanceWsClient + OhlcPipeline
    are all replaced with MagicMock/AsyncMock so the test can assert on
    ``pipeline_mock.start``/``stop`` without touching real I/O.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    if symbols_env is None:
        monkeypatch.delenv("MARKET_DATA_SYMBOLS", raising=False)
    else:
        monkeypatch.setenv("MARKET_DATA_SYMBOLS", symbols_env)
    settings = Settings()  # type: ignore[call-arg]

    pool = MagicMock()
    pool.close = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock())
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)

    bus = MagicMock()
    bus.state = BusConnectionState.CONNECTED
    bus.connect = AsyncMock()
    bus.close = AsyncMock()

    closed = asyncio.Event()
    ws = MagicMock()
    ws.state = WsConnectionState.CONNECTED

    async def _run() -> None:
        await closed.wait()

    async def _close() -> None:
        closed.set()

    ws.run = _run
    ws.close = _close

    pipeline = MagicMock()
    pipeline.start = AsyncMock()
    pipeline.stop = AsyncMock()

    rest = MagicMock()
    rest.close = AsyncMock()

    monkeypatch.setattr(
        "services.market_data.app.main.create_pool",
        AsyncMock(return_value=pool),
    )
    monkeypatch.setattr(
        "services.market_data.app.main.NatsClient",
        MagicMock(return_value=bus),
    )
    monkeypatch.setattr(
        "services.market_data.app.main.BinanceWsClient",
        MagicMock(return_value=ws),
    )
    monkeypatch.setattr(
        "services.market_data.app.main.BinanceRestClient",
        MagicMock(return_value=rest),
    )
    monkeypatch.setattr(
        "services.market_data.app.main.OhlcPipeline",
        MagicMock(return_value=pipeline),
    )

    return create_app(settings=settings), pipeline


# ---------------------------------------------------------------------------
# Factory shape + state attachment
# ---------------------------------------------------------------------------


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
    """`/metrics` returns 200 with default ProcessCollector / PlatformCollector
    / GCCollector series in the body. The exact content-type
    (``text/plain`` vs ``application/openmetrics-text``) is decided by
    ``prometheus_client.make_asgi_app``'s Accept-header negotiation —
    not a T-100 contract — so we assert on body content, not headers.
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


def test_async_state_attached_after_lifespan_entry(
    app_with_mocks: FastAPI,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    mock_ws: MagicMock,
    mock_rest: MagicMock,
) -> None:
    """pool / bus / ws / subscription_mgr / pipeline / rest / backfill land on app.state.

    The TestClient context manager runs the lifespan startup on enter
    and teardown on exit. The ``with`` block here verifies the post-
    startup snapshot of ``app.state``.
    """
    with TestClient(app_with_mocks):
        assert app_with_mocks.state.pool is mock_pool
        assert app_with_mocks.state.bus is mock_bus
        assert app_with_mocks.state.ws is mock_ws
        assert app_with_mocks.state.subscription_mgr is not None
        assert app_with_mocks.state.pipeline is not None
        assert app_with_mocks.state.rest is mock_rest
        assert app_with_mocks.state.backfill is not None


# ---------------------------------------------------------------------------
# Symbol-set passthrough into pipeline.start()
# ---------------------------------------------------------------------------


def test_lifespan_passes_symbols_from_settings_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: MARKET_DATA_SYMBOLS env → Settings.symbols → OhlcPipeline.start(symbols)."""
    app, pipeline = _build_app_with_pipeline_capture(monkeypatch, symbols_env="BTCUSDT,ETHUSDT")
    with TestClient(app):
        pass
    pipeline.start.assert_awaited_once_with(["BTCUSDT", "ETHUSDT"])
    pipeline.stop.assert_awaited_once()


def test_lifespan_with_empty_symbols_calls_pipeline_start_with_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty MARKET_DATA_SYMBOLS → pipeline.start([]) — service stays healthy + ready."""
    app, pipeline = _build_app_with_pipeline_capture(monkeypatch, symbols_env=None)
    with TestClient(app):
        pass
    pipeline.start.assert_awaited_once_with([])
