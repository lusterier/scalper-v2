"""Tests for ``/api/signals/*`` read endpoints (T-403).

Mocks at the router import boundary
(``services.analytics_api.app.routers.signals``) per WG#10 + T-401a/b/T-402
precedent. Pin pagination envelope, Action + IngestionStatus enum
validation (422), JSONB payload passthrough (WG#8), 404 detail format
for detail endpoint (WG#9), and `from`/`to` filter semantics on
received_at column (WG#11).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from packages.core.types import Action, IngestionStatus
from packages.db.queries.analytics import SignalRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_RECEIVED = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    """Type-narrowed accessor for `mock.await_args.kwargs`."""
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def _make_signal(
    *,
    signal_id: int = 1,
    source: str = "tv_rsi_div_v3",
    symbol: str = "BTCUSDT",
    action: Action = Action.LONG,
    ingestion_status: IngestionStatus = IngestionStatus.VALIDATED,
    payload: dict[str, Any] | None = None,
) -> SignalRow:
    return SignalRow(
        id=signal_id,
        received_at=_T_RECEIVED,
        schema_version="1",
        source=source,
        idempotency_key=f"key-{signal_id}",
        symbol=symbol,
        original_symbol=f"{symbol}.P",
        action=action,
        payload=payload if payload is not None else {"price": "50000"},
        ingestion_status=ingestion_status,
        correlation_id=f"cid-{signal_id}",
    )


def _patch_list(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[SignalRow],
    total: int,
) -> tuple[AsyncMock, AsyncMock]:
    select_mock = AsyncMock(return_value=rows)
    count_mock = AsyncMock(return_value=total)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.signals.select_signals_paginated",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.signals.count_signals",
        count_mock,
    )
    return select_mock, count_mock


# ---------------------------------------------------------------------------
# LIST endpoint
# ---------------------------------------------------------------------------


def test_list_signals_returns_200_with_default_pagination(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No query params → limit=50 + offset=0 forwarded to helpers."""
    select_mock, count_mock = _patch_list(monkeypatch, rows=[_make_signal()], total=1)
    response = client.get("/api/signals/")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["total"] == 1
    assert len(body["signals"]) == 1
    select_kwargs = _kwargs_of(select_mock)
    assert select_kwargs["limit"] == 50
    assert select_kwargs["offset"] == 0
    count_mock.assert_awaited_once()


def test_list_signals_envelope_contains_signals_total_limit_offset(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_list(
        monkeypatch,
        rows=[_make_signal(signal_id=1), _make_signal(signal_id=2)],
        total=42,
    )
    body = client.get("/api/signals/?limit=10&offset=20").json()
    assert set(body.keys()) == {"signals", "total", "limit", "offset"}
    assert body["limit"] == 10
    assert body["offset"] == 20
    assert body["total"] == 42


def test_list_signals_applies_limit_and_offset_query_params(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/signals/?limit=25&offset=100")
    kwargs = _kwargs_of(select_mock)
    assert kwargs["limit"] == 25
    assert kwargs["offset"] == 100


def test_list_signals_rejects_limit_over_max(client: TestClient) -> None:
    response = client.get("/api/signals/?limit=999")
    assert response.status_code == 422


def test_list_signals_rejects_negative_offset(client: TestClient) -> None:
    response = client.get("/api/signals/?offset=-1")
    assert response.status_code == 422


def test_list_signals_rejects_garbage_action_via_strenum(client: TestClient) -> None:
    """WG#1 — `?action=garbage` → 422 (Action enum-validated by FastAPI Query)."""
    response = client.get("/api/signals/?action=garbage")
    assert response.status_code == 422


def test_list_signals_rejects_garbage_ingestion_status_via_strenum(
    client: TestClient,
) -> None:
    """WG#2 — `?ingestion_status=garbage` → 422."""
    response = client.get("/api/signals/?ingestion_status=garbage")
    assert response.status_code == 422


def test_list_signals_filters_by_source_and_symbol(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/signals/?source=tv_rsi_div_v3&symbol=BTCUSDT")
    kwargs = _kwargs_of(select_mock)
    assert kwargs["source"] == "tv_rsi_div_v3"
    assert kwargs["symbol"] == "BTCUSDT"


def test_list_signals_filters_by_from_to_iso8601_range(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#11 — `from`/`to` aliases parse to datetime + thread to query helpers."""
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get(
        "/api/signals/?from=2026-05-01T00:00:00%2B00:00&to=2026-05-03T00:00:00%2B00:00",
    )
    kwargs = _kwargs_of(select_mock)
    assert kwargs["from_at"] == datetime(2026, 5, 1, tzinfo=UTC)
    assert kwargs["to_at"] == datetime(2026, 5, 3, tzinfo=UTC)


# ---------------------------------------------------------------------------
# DETAIL endpoint
# ---------------------------------------------------------------------------


def test_get_signal_returns_200_for_existing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.signals.select_signal_by_id",
        AsyncMock(return_value=_make_signal(signal_id=42)),
    )
    response = client.get("/api/signals/42")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 42
    assert body["action"] == "LONG"


def test_get_signal_returns_404_for_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#9 — 404 detail format `f'signal {signal_id} not found'` (no apostrophes for int)."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.signals.select_signal_by_id",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/signals/999")
    assert response.status_code == 404
    assert response.json()["detail"] == "signal 999 not found"


# ---------------------------------------------------------------------------
# JSONB passthrough pin
# ---------------------------------------------------------------------------


def test_signal_payload_jsonb_passthrough_returns_dict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8 — `payload` JSONB renders as JSON object, not escaped string."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.signals.select_signal_by_id",
        AsyncMock(return_value=_make_signal(payload={"price": "50000", "side": "buy"})),
    )
    body = client.get("/api/signals/1").json()
    assert isinstance(body["payload"], dict)
    assert body["payload"] == {"price": "50000", "side": "buy"}
