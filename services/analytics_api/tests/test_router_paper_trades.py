"""Tests for ``/api/paper-trades/*`` read endpoints (T-516a1).

Mirror :mod:`services.analytics_api.tests.test_router_trades` 1:1 modulo
``trades`` → ``paper_trades`` rename. All mocks at the router import boundary
(``services.analytics_api.app.routers.paper_trades``) per WG#7 from
T-401a/b/T-402 precedent. Pin pagination envelope shape (``paper_trades``
key per WG#11), Query validation (TradeStatus enum reuse + limit/offset
bounds), Decimal-as-string serialization (per WG#9), DOUBLE PRECISION as
float, 404 detail format `f'paper trade {id} not found'` (per WG#10),
`from`/`to` filter semantics on closed_at column, and `meta` JSONB
passthrough (read-side via analytics-api lifespan codec).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from packages.core.types import TradeStatus
from packages.db.queries.analytics import PaperTradeRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_OPENED = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
_T_CLOSED = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _make_paper_trade(
    *,
    paper_trade_id: int = 1,
    status: TradeStatus = TradeStatus.CLOSED,
    realized_pnl: Decimal | None = Decimal("12.34"),
    meta: dict[str, object] | None = None,
) -> PaperTradeRow:
    return PaperTradeRow(
        id=paper_trade_id,
        bot_id="alpha",
        signal_id=42,
        open_order_id=100,
        close_order_id=101 if status is not TradeStatus.OPEN else None,
        symbol="BTCUSDT",
        side="buy",
        entry_price=Decimal("50000.0"),
        exit_price=Decimal("50500.0") if status is not TradeStatus.OPEN else None,
        qty=Decimal("0.5"),
        notional_usd=Decimal("25000.0000"),
        realized_pnl=realized_pnl,
        fees_paid=Decimal("0.5000"),
        close_reason="tp" if status is TradeStatus.CLOSED else None,
        opened_at=_T_OPENED,
        closed_at=_T_CLOSED if status is not TradeStatus.OPEN else None,
        status=status,
        mfe_pct=0.025,
        mae_pct=-0.005,
        confidence_score=0.75,
        meta=meta if meta is not None else {},
    )


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    """Type-narrowed accessor for `mock.await_args.kwargs`."""
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def _patch_list(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[PaperTradeRow],
    total: int,
) -> tuple[AsyncMock, AsyncMock]:
    select_mock = AsyncMock(return_value=rows)
    count_mock = AsyncMock(return_value=total)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_paper_trades_paginated",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.count_paper_trades",
        count_mock,
    )
    return select_mock, count_mock


# ---------------------------------------------------------------------------
# LIST endpoint
# ---------------------------------------------------------------------------


def test_list_paper_trades_returns_200_with_default_pagination(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No query params → limit=50 + offset=0 forwarded to helpers."""
    select_mock, count_mock = _patch_list(monkeypatch, rows=[_make_paper_trade()], total=1)
    response = client.get("/api/paper-trades/")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["total"] == 1
    assert len(body["paper_trades"]) == 1
    select_kwargs = _kwargs_of(select_mock)
    assert select_kwargs["limit"] == 50
    assert select_kwargs["offset"] == 0
    count_mock.assert_awaited_once()


def test_list_paper_trades_envelope_contains_paper_trades_total_limit_offset(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#11 — envelope key MUST be ``paper_trades`` (NOT ``trades``)."""
    _patch_list(
        monkeypatch,
        rows=[_make_paper_trade(paper_trade_id=1), _make_paper_trade(paper_trade_id=2)],
        total=42,
    )
    body = client.get("/api/paper-trades/?limit=10&offset=20").json()
    assert set(body.keys()) == {"paper_trades", "total", "limit", "offset"}
    assert body["limit"] == 10
    assert body["offset"] == 20
    assert body["total"] == 42


def test_list_paper_trades_applies_limit_and_offset_query_params(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/paper-trades/?limit=25&offset=100")
    kwargs = _kwargs_of(select_mock)
    assert kwargs["limit"] == 25
    assert kwargs["offset"] == 100


def test_list_paper_trades_rejects_limit_over_max(client: TestClient) -> None:
    """Query(le=200) → 422 on `?limit=999`."""
    response = client.get("/api/paper-trades/?limit=999")
    assert response.status_code == 422


def test_list_paper_trades_rejects_negative_offset(client: TestClient) -> None:
    """Query(ge=0) → 422 on `?offset=-1`."""
    response = client.get("/api/paper-trades/?offset=-1")
    assert response.status_code == 422


def test_list_paper_trades_rejects_garbage_status_via_strenum(
    client: TestClient,
) -> None:
    """`?status=garbage` → 422 (TradeStatus enum-validated by FastAPI Query)."""
    response = client.get("/api/paper-trades/?status=garbage")
    assert response.status_code == 422


def test_list_paper_trades_filters_by_bot_id(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/paper-trades/?bot_id=alpha")
    assert _kwargs_of(select_mock)["bot_id"] == "alpha"


def test_list_paper_trades_filters_by_symbol_and_status(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/paper-trades/?symbol=BTCUSDT&status=closed")
    kwargs = _kwargs_of(select_mock)
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["status"] is TradeStatus.CLOSED


def test_list_paper_trades_filters_by_from_to_iso8601_range(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`?from=` + `?to=` aliases parse to datetime + thread to query helpers."""
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get(
        "/api/paper-trades/?from=2026-05-01T00:00:00%2B00:00&to=2026-05-03T00:00:00%2B00:00",
    )
    kwargs = _kwargs_of(select_mock)
    assert kwargs["from_at"] == datetime(2026, 5, 1, tzinfo=UTC)
    assert kwargs["to_at"] == datetime(2026, 5, 3, tzinfo=UTC)


def test_list_paper_trades_offset_beyond_total_returns_empty(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offset past total → empty list + total still reflects unpaginated count."""
    _patch_list(monkeypatch, rows=[], total=5)
    body = client.get("/api/paper-trades/?offset=100").json()
    assert body["paper_trades"] == []
    assert body["total"] == 5


# ---------------------------------------------------------------------------
# DETAIL endpoint
# ---------------------------------------------------------------------------


def test_get_paper_trade_returns_200_for_existing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_paper_trade_by_id",
        AsyncMock(return_value=_make_paper_trade(paper_trade_id=42)),
    )
    response = client.get("/api/paper-trades/42")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 42
    assert body["status"] == "closed"


def test_get_paper_trade_returns_404_for_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#10 — 404 detail format `f'paper trade {paper_trade_id} not found'`."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_paper_trade_by_id",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/paper-trades/999")
    assert response.status_code == 404
    assert response.json()["detail"] == "paper trade 999 not found"


# ---------------------------------------------------------------------------
# Decimal precision + DOUBLE PRECISION + JSONB passthrough pins
# ---------------------------------------------------------------------------


def test_paper_trade_decimal_fields_serialize_as_string(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#9 + §N1 / §5.3 — NUMERIC columns render as JSON strings, not floats."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_paper_trade_by_id",
        AsyncMock(return_value=_make_paper_trade(paper_trade_id=1)),
    )
    body = client.get("/api/paper-trades/1").json()
    assert isinstance(body["entry_price"], str)
    assert isinstance(body["exit_price"], str)
    assert isinstance(body["qty"], str)
    assert isinstance(body["realized_pnl"], str)
    assert isinstance(body["fees_paid"], str)
    assert body["qty"] == "0.5"
    assert body["realized_pnl"] == "12.34"


def test_paper_trade_double_precision_fields_serialize_as_float(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DOUBLE PRECISION columns (mfe_pct/mae_pct/confidence_score) → JSON numbers."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_paper_trade_by_id",
        AsyncMock(return_value=_make_paper_trade(paper_trade_id=1)),
    )
    body = client.get("/api/paper-trades/1").json()
    assert isinstance(body["mfe_pct"], float)
    assert isinstance(body["mae_pct"], float)
    assert isinstance(body["confidence_score"], float)
    assert body["mfe_pct"] == 0.025


def test_paper_trade_meta_jsonb_passthrough_returns_dict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`meta` JSONB renders as JSON object, not escaped string."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_paper_trade_by_id",
        AsyncMock(return_value=_make_paper_trade(meta={"strategy": "v3", "risk_tier": 1})),
    )
    body = client.get("/api/paper-trades/1").json()
    assert isinstance(body["meta"], dict)
    assert body["meta"] == {"strategy": "v3", "risk_tier": 1}


def test_paper_trade_open_status_returns_null_close_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open paper-trade → exit_price / closed_at / close_reason / close_order_id all null."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_paper_trade_by_id",
        AsyncMock(
            return_value=_make_paper_trade(status=TradeStatus.OPEN, realized_pnl=None),
        ),
    )
    body = client.get("/api/paper-trades/1").json()
    assert body["status"] == "open"
    assert body["exit_price"] is None
    assert body["closed_at"] is None
    assert body["close_reason"] is None
    assert body["close_order_id"] is None
    assert body["realized_pnl"] is None
