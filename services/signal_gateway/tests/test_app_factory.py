"""Tests for :func:`services.signal_gateway.app.main.create_app`."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.signal_gateway.app.config import Settings
from services.signal_gateway.app.dedup import DedupRing
from services.signal_gateway.app.main import create_app
from services.signal_gateway.app.metrics import Metrics
from services.signal_gateway.app.middleware import RateLimitMiddleware
from services.signal_gateway.app.rate_limit import RateLimiter

if TYPE_CHECKING:
    import pytest


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
    and is stable across prometheus_client versions. T-015b2a expanded
    the registry with service counters; this assertion still only
    verifies the scrape surface is live, not the full metric set.
    """
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "python_info" in response.text


def test_app_exposes_webhook_route(app_with_mocks: FastAPI) -> None:
    """``POST /webhook`` is wired via :mod:`.webhook` router (T-015b2b)."""
    assert "/webhook" in _route_paths(app_with_mocks)


# ---- T-015b2a additions ----------------------------------------------------


def test_sync_state_attached_after_create_app(app_with_mocks: FastAPI) -> None:
    """T-015b2a sync primitives land on app.state immediately after create_app()."""
    s = app_with_mocks.state
    assert isinstance(s.settings, Settings)
    assert isinstance(s.metrics, Metrics)
    assert isinstance(s.rate_limiter, RateLimiter)
    assert isinstance(s.dedup, DedupRing)
    assert s.logger is not None
    assert s.trading_logger is not None


def test_async_state_attached_after_lifespan(client: TestClient) -> None:
    """T-015a + T-015b2a async resources land on app.state inside lifespan.

    ``client`` enters the TestClient context manager which runs the
    lifespan, so by the time this test executes ``pool``, ``bus``, and
    ``symbol_cache`` have all been attached.
    """
    app = cast("FastAPI", client.app)
    s = app.state
    assert s.pool is not None
    assert s.bus is not None
    assert s.symbol_cache is not None


def test_rate_limit_middleware_registered(app_with_mocks: FastAPI) -> None:
    """RateLimitMiddleware is in the middleware chain (T-015b2a)."""
    # m.cls is typed as Starlette's _MiddlewareFactory[P]; mypy can't
    # reconcile that with a concrete class identity check. Comparison
    # is correct at runtime — the factory IS the class object.
    assert any(
        m.cls is RateLimitMiddleware  # type: ignore[comparison-overlap]
        for m in app_with_mocks.user_middleware
    )


def test_trace_middleware_runs_before_rate_limit_middleware(
    settings: Settings,
    mock_pool: MagicMock,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trace_scope must be outermost — its ``X-Request-ID`` survives a 429.

    Functional verification: force the rate limiter to reject, then
    assert the response carries the trace header. The header is set
    only on the way out by ``bind_trace``; if the chain order is
    inverted, the 429 produced by ``RateLimitMiddleware`` would
    short-circuit before ``bind_trace`` got to set the header. If
    this test fails, swap the ``add_middleware`` and
    ``@app.middleware("http")`` registrations in ``main.py``.
    """
    monkeypatch.setattr(
        "services.signal_gateway.app.main.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr(
        "services.signal_gateway.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    app = create_app(settings=settings)

    # Patch on the limiter instance after middleware construction works because
    # RateLimitMiddleware holds an object reference (self._limiter) and looks up
    # check_and_record at dispatch time, not bind time. If middleware ever caches
    # the bound method, this patch strategy needs updating.
    rate_limiter = cast("RateLimiter", app.state.rate_limiter)
    rate_limiter.check_and_record = AsyncMock(  # type: ignore[method-assign]
        return_value=False,
    )
    with TestClient(app) as c:
        response = c.post(
            "/webhook",
            content=b"{}",
            headers={"X-Real-IP": "1.2.3.4"},
        )
    assert response.status_code == 429
    assert response.headers.get("X-Request-ID"), (
        "trace_scope did NOT run on the 429 response — "
        "RateLimitMiddleware is currently outermost. Swap "
        "registration order in create_app()."
    )
