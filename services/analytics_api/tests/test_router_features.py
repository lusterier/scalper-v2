"""Tests for ``/api/features/*`` read endpoints (T-404).

Mocks at the router import boundary
(``services.analytics_api.app.routers.features``) per WG#9 + T-401a/b/T-402/T-403
precedent. Pin envelope shape, prefix filter, limit/offset bounds (422),
required feature_name+symbol Query params (422 if missing), DOUBLE
PRECISION → float, JSONB passthrough (object + array), empty list = 200.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from packages.db.queries.analytics import FeatureRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_NOW = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    """Type-narrowed accessor for `mock.await_args.kwargs`."""
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def _make_feature(
    *,
    feature_name: str = "ind.btcusdt.15m.ema_20",
    symbol: str = "BTCUSDT",
    value_num: float | None = 50000.0,
    value_bool: bool | None = None,
    value_json: dict[str, Any] | list[Any] | None = None,
) -> FeatureRow:
    return FeatureRow(
        feature_name=feature_name,
        symbol=symbol,
        computed_at=_T_NOW,
        value_num=value_num,
        value_bool=value_bool,
        value_json=value_json,
        source_version="builtin.ema.v1",
    )


def _patch_latest(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[FeatureRow],
    total: int,
) -> tuple[AsyncMock, AsyncMock]:
    select_mock = AsyncMock(return_value=rows)
    count_mock = AsyncMock(return_value=total)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.features.select_latest_features",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.features.count_latest_features",
        count_mock,
    )
    return select_mock, count_mock


def _patch_history(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[FeatureRow],
    total: int,
) -> tuple[AsyncMock, AsyncMock]:
    select_mock = AsyncMock(return_value=rows)
    count_mock = AsyncMock(return_value=total)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.features.select_features_history",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.features.count_features_history",
        count_mock,
    )
    return select_mock, count_mock


# ---------------------------------------------------------------------------
# /api/features/latest
# ---------------------------------------------------------------------------


def test_list_latest_features_returns_200_with_default_pagination(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, count_mock = _patch_latest(monkeypatch, [_make_feature()], total=1)
    response = client.get("/api/features/latest")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert body["total"] == 1
    assert len(body["features"]) == 1
    select_kwargs = _kwargs_of(select_mock)
    assert select_kwargs["limit"] == 100
    assert select_kwargs["offset"] == 0
    assert select_kwargs["prefix"] is None
    count_mock.assert_awaited_once()


def test_list_latest_features_envelope_contains_all_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latest(monkeypatch, [], total=42)
    body = client.get("/api/features/latest?limit=10&offset=20").json()
    assert set(body.keys()) == {"features", "total", "limit", "offset"}
    assert body["limit"] == 10
    assert body["offset"] == 20
    assert body["total"] == 42


def test_list_latest_features_filters_by_prefix_query_param(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_latest(monkeypatch, [], total=0)
    client.get("/api/features/latest?prefix=ind.btcusdt")
    assert _kwargs_of(select_mock)["prefix"] == "ind.btcusdt"


def test_list_latest_features_rejects_limit_over_max_500(client: TestClient) -> None:
    response = client.get("/api/features/latest?limit=501")
    assert response.status_code == 422


def test_list_latest_features_rejects_negative_offset(client: TestClient) -> None:
    response = client.get("/api/features/latest?offset=-1")
    assert response.status_code == 422


def test_list_latest_features_returns_empty_envelope_when_no_features(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8 — empty list = 200 with total=0 (NOT 404)."""
    _patch_latest(monkeypatch, [], total=0)
    response = client.get("/api/features/latest")
    assert response.status_code == 200
    body = response.json()
    assert body == {"features": [], "total": 0, "limit": 100, "offset": 0}


# ---------------------------------------------------------------------------
# /api/features/history
# ---------------------------------------------------------------------------


def test_list_feature_history_returns_200_with_default_pagination(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, count_mock = _patch_history(monkeypatch, [_make_feature()], total=1)
    response = client.get(
        "/api/features/history?feature_name=ind.btcusdt.15m.ema_20&symbol=BTCUSDT",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 1000
    assert body["offset"] == 0
    assert body["total"] == 1
    select_kwargs = _kwargs_of(select_mock)
    assert select_kwargs["limit"] == 1000
    assert select_kwargs["offset"] == 0
    assert select_kwargs["feature_name"] == "ind.btcusdt.15m.ema_20"
    assert select_kwargs["symbol"] == "BTCUSDT"
    count_mock.assert_awaited_once()


def test_list_feature_history_rejects_missing_feature_name(client: TestClient) -> None:
    response = client.get("/api/features/history?symbol=BTCUSDT")
    assert response.status_code == 422


def test_list_feature_history_rejects_missing_symbol(client: TestClient) -> None:
    response = client.get("/api/features/history?feature_name=ind.btcusdt.15m.ema_20")
    assert response.status_code == 422


def test_list_feature_history_rejects_limit_over_max_5000(client: TestClient) -> None:
    response = client.get(
        "/api/features/history?feature_name=ind.btcusdt.15m.ema_20&symbol=BTCUSDT&limit=5001",
    )
    assert response.status_code == 422


def test_list_feature_history_filters_by_from_to_iso8601_range(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#5 — `from`/`to` aliases parse to datetime; half-open semantics."""
    select_mock, _ = _patch_history(monkeypatch, [], total=0)
    client.get(
        "/api/features/history"
        "?feature_name=ind.btcusdt.15m.ema_20&symbol=BTCUSDT"
        "&from=2026-05-01T00:00:00%2B00:00&to=2026-05-03T00:00:00%2B00:00",
    )
    kwargs = _kwargs_of(select_mock)
    assert kwargs["from_at"] == datetime(2026, 5, 1, tzinfo=UTC)
    assert kwargs["to_at"] == datetime(2026, 5, 3, tzinfo=UTC)


def test_feature_history_returns_empty_envelope_when_no_rows_in_range(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty range = 200 with empty list (NOT 404 — collection-shape)."""
    _patch_history(monkeypatch, [], total=0)
    response = client.get(
        "/api/features/history?feature_name=ind.btcusdt.15m.ema_20&symbol=BTCUSDT",
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"features": [], "total": 0, "limit": 1000, "offset": 0}


# ---------------------------------------------------------------------------
# Value polymorphism (3 columns) + DOUBLE PRECISION + JSONB passthrough
# ---------------------------------------------------------------------------


def test_feature_value_num_serializes_as_float(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#7 — DOUBLE PRECISION value_num → JSON float, not Decimal-string."""
    _patch_latest(monkeypatch, [_make_feature(value_num=50000.123)], total=1)
    body = client.get("/api/features/latest").json()
    f = body["features"][0]
    assert isinstance(f["value_num"], float)
    assert f["value_num"] == 50000.123


def test_feature_value_bool_serializes_as_bool(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_latest(
        monkeypatch,
        [_make_feature(value_num=None, value_bool=True)],
        total=1,
    )
    body = client.get("/api/features/latest").json()
    f = body["features"][0]
    assert f["value_bool"] is True
    assert f["value_num"] is None


def test_feature_value_json_object_passthrough(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#6 — `value_json` JSONB object renders as JSON dict."""
    _patch_latest(
        monkeypatch,
        [_make_feature(value_num=None, value_json={"k": "v", "n": 1})],
        total=1,
    )
    body = client.get("/api/features/latest").json()
    f = body["features"][0]
    assert isinstance(f["value_json"], dict)
    assert f["value_json"] == {"k": "v", "n": 1}


def test_feature_value_json_array_passthrough(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#6 — `value_json` JSONB array renders as JSON list (not coerced to dict)."""
    _patch_latest(
        monkeypatch,
        [_make_feature(value_num=None, value_json=[1, 2, 3])],
        total=1,
    )
    body = client.get("/api/features/latest").json()
    f = body["features"][0]
    assert isinstance(f["value_json"], list)
    assert f["value_json"] == [1, 2, 3]
