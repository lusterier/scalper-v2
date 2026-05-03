"""Tests for ``/api/audit/*`` read endpoints (T-405).

Mocks at the router import boundary
(``services.analytics_api.app.routers.audit``) per WG#15 + T-401b
precedent. Pin envelope shape, action_prefix LIKE filter, from/to range,
422 bounds, composite-PK detail with required ``?occurred_at=`` (WG#5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from packages.db.queries.audit import AuditEventRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_EVT = datetime(2026, 5, 3, 12, 0, 1, tzinfo=UTC)


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def _make_audit(
    *,
    event_id: int = 1,
    actor: str = "lan:127.0.0.1",
    action: str = "symbol_map.create",
    entity_type: str = "symbol_map",
    entity_id: str = "BTCUSDT.P",
) -> AuditEventRow:
    return AuditEventRow(
        id=event_id,
        occurred_at=_T_EVT,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=None,
        after_state={"input_symbol": "BTCUSDT.P", "canonical_symbol": "BTCUSDT"},
        correlation_id=f"cid-{event_id}",
        meta={},
    )


def _patch_list(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[AuditEventRow],
    total: int,
) -> tuple[AsyncMock, AsyncMock]:
    select_mock = AsyncMock(return_value=rows)
    count_mock = AsyncMock(return_value=total)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.audit.select_audit_events_paginated",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.audit.count_audit_events",
        count_mock,
    )
    return select_mock, count_mock


def test_list_audit_events_returns_200_with_default_pagination(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, count_mock = _patch_list(monkeypatch, [_make_audit()], total=1)
    response = client.get("/api/audit/")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["total"] == 1
    select_kwargs = _kwargs_of(select_mock)
    assert select_kwargs["limit"] == 50
    count_mock.assert_awaited_once()


def test_list_audit_envelope_shape(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_list(monkeypatch, [], total=42)
    body = client.get("/api/audit/?limit=10&offset=20").json()
    assert set(body.keys()) == {"events", "total", "limit", "offset"}


def test_list_audit_filters_by_entity_type(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, [], total=0)
    client.get("/api/audit/?entity_type=symbol_map")
    assert _kwargs_of(select_mock)["entity_type"] == "symbol_map"


def test_list_audit_filters_by_action_prefix(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`?action_prefix=bot_config.` matches all bot_config.* events."""
    select_mock, _ = _patch_list(monkeypatch, [], total=0)
    client.get("/api/audit/?action_prefix=bot_config.")
    assert _kwargs_of(select_mock)["action_prefix"] == "bot_config."


def test_list_audit_filters_by_from_to_iso8601_range(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, [], total=0)
    client.get(
        "/api/audit/?from=2026-05-01T00:00:00%2B00:00&to=2026-05-03T00:00:00%2B00:00",
    )
    kwargs = _kwargs_of(select_mock)
    assert kwargs["from_at"] == datetime(2026, 5, 1, tzinfo=UTC)
    assert kwargs["to_at"] == datetime(2026, 5, 3, tzinfo=UTC)


def test_list_audit_rejects_limit_over_max(client: TestClient) -> None:
    response = client.get("/api/audit/?limit=999")
    assert response.status_code == 422


def test_list_audit_rejects_negative_offset(client: TestClient) -> None:
    response = client.get("/api/audit/?offset=-1")
    assert response.status_code == 422


def test_get_audit_event_returns_200_with_full_row(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.audit.select_audit_event_by_id",
        AsyncMock(return_value=_make_audit(event_id=42)),
    )
    response = client.get(
        "/api/audit/42?occurred_at=2026-05-03T12:00:01%2B00:00",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 42
    assert isinstance(body["meta"], dict)
    assert isinstance(body["after_state"], dict)


def test_get_audit_event_requires_occurred_at_query_param(client: TestClient) -> None:
    """WG#5 — occurred_at is required Query param for hypertable chunk pruning."""
    response = client.get("/api/audit/42")
    assert response.status_code == 422


def test_get_audit_event_returns_404_when_no_match(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.audit.select_audit_event_by_id",
        AsyncMock(return_value=None),
    )
    response = client.get(
        "/api/audit/999?occurred_at=2026-05-03T12:00:01%2B00:00",
    )
    assert response.status_code == 404
    assert "999" in response.json()["detail"]
