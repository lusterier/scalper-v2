"""Tests for ``/api/analytics/*`` endpoints (T-406).

13 tests covering: 4 endpoints + cache integration + Decimal-as-string
serialization + WG#7 pre-validate cap + 422 bounds.

All mocks at router import boundary per T-401b/T-402/.../T-405 precedent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from packages.db.queries.analytics import TradeRealizedPnlRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_BASE = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _row(pnl: str, *, closed_at: datetime = _T_BASE) -> TradeRealizedPnlRow:
    return TradeRealizedPnlRow(
        realized_pnl=Decimal(pnl),
        closed_at=closed_at,
        bot_id="alpha",
    )


def _patch_select(monkeypatch: pytest.MonkeyPatch, rows: list[TradeRealizedPnlRow]) -> AsyncMock:
    mock = AsyncMock(return_value=rows)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.analytics.select_trades_for_analytics",
        mock,
    )
    return mock


# ---------------------------------------------------------------------------
# /api/analytics/expectancy
# ---------------------------------------------------------------------------


def test_get_expectancy_returns_200_with_metrics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_select(monkeypatch, [_row("10"), _row("-5"), _row("3")])
    response = client.get("/api/analytics/expectancy?bot_id=alpha")
    assert response.status_code == 200
    body = response.json()
    assert body["total_trades"] == 3
    assert body["win_count"] == 2
    assert body["loss_count"] == 1
    # Decimal fields → JSON string per §5.3.
    assert isinstance(body["avg_win"], str)
    assert isinstance(body["avg_loss"], str)
    # Statistical metrics → float per §5.13.
    assert isinstance(body["expectancy"], float)
    assert isinstance(body["win_rate"], float)


def test_get_expectancy_empty_window_returns_zero_metrics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_select(monkeypatch, [])
    body = client.get("/api/analytics/expectancy").json()
    assert body["total_trades"] == 0
    assert body["expectancy"] == 0.0
    assert body["win_rate"] == 0.0


def test_get_expectancy_filters_by_bot_id(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock = _patch_select(monkeypatch, [])
    client.get("/api/analytics/expectancy?bot_id=beta")
    assert select_mock.await_args is not None
    assert select_mock.await_args.kwargs["bot_id"] == "beta"


# ---------------------------------------------------------------------------
# /api/analytics/heatmap/hourly
# ---------------------------------------------------------------------------


def test_get_hourly_heatmap_returns_168_cells(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_select(monkeypatch, [])
    body = client.get("/api/analytics/heatmap/hourly").json()
    assert len(body["cells"]) == 168


def test_get_hourly_heatmap_filters_by_from_to(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock = _patch_select(monkeypatch, [])
    client.get(
        "/api/analytics/heatmap/hourly"
        "?from=2026-05-01T00:00:00%2B00:00&to=2026-05-03T00:00:00%2B00:00",
    )
    assert select_mock.await_args is not None
    kwargs = select_mock.await_args.kwargs
    assert kwargs["from_at"] == datetime(2026, 5, 1, tzinfo=UTC)
    assert kwargs["to_at"] == datetime(2026, 5, 3, tzinfo=UTC)


# ---------------------------------------------------------------------------
# /api/analytics/pnl-series
# ---------------------------------------------------------------------------


def test_get_pnl_series_returns_200_with_day_bucket(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_select(
        monkeypatch,
        [
            _row("5", closed_at=datetime(2026, 5, 1, 9, 0, tzinfo=UTC)),
            _row("3", closed_at=datetime(2026, 5, 2, 10, 0, tzinfo=UTC)),
        ],
    )
    body = client.get("/api/analytics/pnl-series?bucket=day").json()
    assert len(body["points"]) == 2
    # Decimal fields → JSON string per §5.3.
    assert isinstance(body["points"][0]["bucket_pnl"], str)
    assert isinstance(body["points"][0]["cumulative_pnl"], str)


def test_get_pnl_series_rejects_invalid_bucket(client: TestClient) -> None:
    """`?bucket=garbage` → 422 (Literal Query validation)."""
    response = client.get("/api/analytics/pnl-series?bucket=garbage")
    assert response.status_code == 422


def test_get_pnl_series_rejects_window_exceeding_5000_points(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#7 — pre-validate cap: huge window returns 422 BEFORE DB query."""
    select_mock = _patch_select(monkeypatch, [])
    # 5001 hours = 208+ days; capped at 5000.
    response = client.get(
        "/api/analytics/pnl-series"
        "?bucket=hour&from=2020-01-01T00:00:00%2B00:00&to=2026-01-01T00:00:00%2B00:00",
    )
    assert response.status_code == 422
    assert "buckets" in response.json()["detail"].lower() or "5000" in response.json()["detail"]
    select_mock.assert_not_awaited()  # WG#7: no DB hit


# ---------------------------------------------------------------------------
# /api/analytics/monte-carlo
# ---------------------------------------------------------------------------


def test_post_monte_carlo_returns_200_with_percentiles(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_select(
        monkeypatch,
        [_row(str(v)) for v in [1, 2, 3, -1, 5]],
    )
    body = client.post("/api/analytics/monte-carlo?n_simulations=100").json()
    assert "p5" in body
    assert "p50" in body
    assert "p95" in body
    assert body["n_simulations"] == 100
    # Decimal percentiles → JSON string per §5.3.
    assert isinstance(body["p50"], str)


def test_post_monte_carlo_rejects_n_over_max(client: TestClient) -> None:
    """`?n_simulations=10001` → 422 (Query le=10000)."""
    response = client.post("/api/analytics/monte-carlo?n_simulations=10001")
    assert response.status_code == 422


def test_post_monte_carlo_rejects_zero_n(client: TestClient) -> None:
    response = client.post("/api/analytics/monte-carlo?n_simulations=0")
    assert response.status_code == 422


def test_post_monte_carlo_uses_cache_within_ttl(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache hit on second identical request — DB select called once."""
    select_mock = _patch_select(monkeypatch, [_row("5"), _row("-3"), _row("8")])
    body1 = client.post("/api/analytics/monte-carlo?n_simulations=50&bot_id=alpha").json()
    body2 = client.post("/api/analytics/monte-carlo?n_simulations=50&bot_id=alpha").json()
    # Same percentiles (cache hit OR seed determinism).
    assert body1["p50"] == body2["p50"]
    # DB called exactly once due to cache.
    assert select_mock.await_count == 1


def test_post_monte_carlo_seed_deterministic_for_same_request_shape(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two calls with identical params → identical percentiles (cache + seed determinism)."""
    _patch_select(
        monkeypatch,
        [_row(str(v)) for v in [1, 2, 3, 4, 5, -1, -2]],
    )
    body1 = client.post("/api/analytics/monte-carlo?n_simulations=200&bot_id=gamma").json()
    body2 = client.post("/api/analytics/monte-carlo?n_simulations=200&bot_id=gamma").json()
    assert body1["seed"] == body2["seed"]
    assert body1["p5"] == body2["p5"]
    assert body1["p95"] == body2["p95"]
