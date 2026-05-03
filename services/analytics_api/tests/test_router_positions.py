"""Tests for ``/api/positions/*`` read endpoint (T-402).

All mocks at the router import boundary
(``services.analytics_api.app.routers.positions``) per WG#8 from
T-401a/b. Decimal serialization pin per WG#7: NUMERIC fields render
as JSON strings (preserves precision per §N1 / §5.3); never floats.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from packages.db.queries.analytics import OpenPositionRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_UPDATED = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)


def _make_position(
    *,
    bot_id: str = "alpha",
    symbol: str = "BTCUSDT",
    sl_price: Decimal | None = Decimal("45000.0"),
    sl_type: str | None = "protective",
) -> OpenPositionRow:
    return OpenPositionRow(
        bot_id=bot_id,
        symbol=symbol,
        trade_id=1,
        side="buy",
        entry_price=Decimal("50000.123456789012"),
        qty=Decimal("0.5"),
        remaining_qty=Decimal("0.5"),
        sl_price=sl_price,
        tp_price=Decimal("55000.0"),
        sl_type=sl_type,
        best_price=Decimal("50100.0"),
        tp_hit=False,
        trailing_active=False,
        running_pnl=Decimal("0.0"),
        mfe_price=Decimal("50100.0"),
        mae_price=Decimal("49900.0"),
        updated_at=_T_UPDATED,
    )


def test_list_positions_returns_200_with_all_when_no_filter(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/positions/ with no query → all open positions returned."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.positions.select_open_positions",
        AsyncMock(
            return_value=[
                _make_position(bot_id="alpha", symbol="BTCUSDT"),
                _make_position(bot_id="beta", symbol="ETHUSDT"),
            ]
        ),
    )
    response = client.get("/api/positions/")
    assert response.status_code == 200
    body = response.json()
    assert len(body["positions"]) == 2
    assert body["positions"][0]["bot_id"] == "alpha"
    assert body["positions"][1]["bot_id"] == "beta"


def test_list_positions_filters_by_bot_id_query_param(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`?bot_id=alpha` threads through to query helper."""
    select_mock = AsyncMock(return_value=[_make_position(bot_id="alpha")])
    monkeypatch.setattr(
        "services.analytics_api.app.routers.positions.select_open_positions",
        select_mock,
    )
    response = client.get("/api/positions/?bot_id=alpha")
    assert response.status_code == 200
    select_mock.assert_awaited_once()
    assert select_mock.await_args is not None
    assert select_mock.await_args.kwargs == {"bot_id": "alpha"}


def test_list_positions_returns_empty_envelope_when_no_open(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.positions.select_open_positions",
        AsyncMock(return_value=[]),
    )
    response = client.get("/api/positions/")
    assert response.status_code == 200
    assert response.json() == {"positions": []}


def test_position_decimal_fields_serialize_as_string(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#7 + §N1 / §5.3 — Decimal columns render as JSON strings, not floats."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.positions.select_open_positions",
        AsyncMock(return_value=[_make_position()]),
    )
    response = client.get("/api/positions/")
    body = response.json()
    pos = body["positions"][0]
    assert isinstance(pos["entry_price"], str)
    assert isinstance(pos["qty"], str)
    assert isinstance(pos["running_pnl"], str)
    # Pin verbatim: precision-preserving string (not float repr).
    assert pos["qty"] == "0.5"
    assert pos["entry_price"] == "50000.123456789012"


def test_position_optional_fields_null_in_response(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sl_price=None / sl_type=None → JSON `null`, not missing key."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.positions.select_open_positions",
        AsyncMock(return_value=[_make_position(sl_price=None, sl_type=None)]),
    )
    response = client.get("/api/positions/")
    pos = response.json()["positions"][0]
    assert pos["sl_price"] is None
    assert pos["sl_type"] is None
