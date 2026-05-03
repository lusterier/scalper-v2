"""Tests for ``/api/bots/*`` read endpoints (T-401a).

Mocks ``select_all_bots`` / ``select_bot_by_id`` at the router import
boundary (`services.analytics_api.app.routers.bots`) per WG#8 +
T-400 conftest:84-90 precedent. Patching at the router boundary
verifies the router-level import is correct, not just the underlying
query function.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from packages.core.types import BotStatus, ExchangeMode
from packages.db.queries.analytics import BotDetailRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_CREATED = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
_T_APPLIED = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _make_bot_row(
    bot_id: str,
    *,
    status: BotStatus = BotStatus.ACTIVE,
    exchange_mode: ExchangeMode = ExchangeMode.PAPER,
    meta: dict[str, Any] | None = None,
) -> BotDetailRow:
    return BotDetailRow(
        bot_id=bot_id,
        display_name=f"Bot {bot_id}",
        created_at=_T_CREATED,
        status=status,
        exchange_mode=exchange_mode,
        config_hash="deadbeef" * 8,
        config_applied_at=_T_APPLIED,
        meta=meta if meta is not None else {},
    )


def test_list_bots_returns_200_with_all_rows(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/bots/ → 200 with 2 BotResponse entries in caller order."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.bots.select_all_bots",
        AsyncMock(
            return_value=[
                _make_bot_row("alpha", exchange_mode=ExchangeMode.PAPER),
                _make_bot_row("beta", status=BotStatus.PAUSED, exchange_mode=ExchangeMode.LIVE),
            ]
        ),
    )
    response = client.get("/api/bots/")
    assert response.status_code == 200
    body = response.json()
    assert len(body["bots"]) == 2
    assert body["bots"][0]["bot_id"] == "alpha"
    assert body["bots"][0]["status"] == "active"
    assert body["bots"][0]["exchange_mode"] == "paper"
    assert body["bots"][1]["bot_id"] == "beta"
    assert body["bots"][1]["status"] == "paused"
    assert body["bots"][1]["exchange_mode"] == "live"


def test_list_bots_returns_empty_list_when_no_bots(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/bots/ → 200 with empty bots array (NOT 404)."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.bots.select_all_bots",
        AsyncMock(return_value=[]),
    )
    response = client.get("/api/bots/")
    assert response.status_code == 200
    assert response.json() == {"bots": []}


def test_get_bot_returns_200_for_existing_bot(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/bots/{bot_id} → 200 with all 8 fields populated."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.bots.select_bot_by_id",
        AsyncMock(
            return_value=_make_bot_row(
                "alpha",
                meta={"foo": "bar"},
            )
        ),
    )
    response = client.get("/api/bots/alpha")
    assert response.status_code == 200
    body = response.json()
    assert body["bot_id"] == "alpha"
    assert body["display_name"] == "Bot alpha"
    assert body["status"] == "active"
    assert body["exchange_mode"] == "paper"
    assert body["config_hash"] == "deadbeef" * 8
    assert body["meta"] == {"foo": "bar"}
    # Datetime serialisation: Pydantic emits ISO-8601 with offset.
    assert body["created_at"].startswith("2026-04-20T00:00:00")
    assert body["config_applied_at"].startswith("2026-05-02T12:00:00")


def test_get_bot_returns_404_for_missing_bot(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/bots/{bot_id} → 404 with bot_id in detail when None returned."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.bots.select_bot_by_id",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/bots/nonexistent")
    assert response.status_code == 404
    body = response.json()
    assert "nonexistent" in body["detail"]
    assert "not found" in body["detail"]
