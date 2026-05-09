"""Tests for ``/api/trades/{id}/shadow-variants`` + ``/api/paper-trades/{id}/...`` (T-516b).

Mock-based: ``select_shadow_variants_by_parent`` patched at the router
import boundary per the ``test_router_paper_trades.py:75-78`` precedent.
Pin:

* parent_kind discriminator hardcoded per route (NOT a query param;
  cannot be tampered with from URL/body) per ADR-0010.
* Empty result returns 200 with ``{ variants: [] }`` envelope (NOT 404).
* Pydantic ``ShadowVariantResponse`` Decimal-as-string serialization
  (entry_price / qty / realized_pnl per §5.3).
* parent_kind kwarg pin: live route → ``parent_kind="live"``; paper
  route → ``parent_kind="paper"`` (per Write-time guidance #7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from packages.core.types import ShadowVariantTerminal
from packages.db.queries.shadow import ShadowVariantRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_CREATED = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
_T_TERMINATED = datetime(2026, 5, 1, 10, 30, 0, tzinfo=UTC)


def _make_variant(
    *,
    row_id: int = 1,
    parent_trade_id: int = 7,
    parent_kind: str = "live",
    variant_name: str = "no_be",
    terminated_at: datetime | None = None,
    terminal_outcome: ShadowVariantTerminal | None = None,
    realized_pnl: Decimal | None = None,
) -> ShadowVariantRow:
    return ShadowVariantRow(
        id=row_id,
        parent_trade_id=parent_trade_id,
        bot_id="alpha",
        variant_name=variant_name,
        side="buy",
        entry_price=Decimal("50000.00"),
        qty=Decimal("0.001"),
        created_at=_T_CREATED,
        terminated_at=terminated_at,
        terminal_outcome=terminal_outcome,
        realized_pnl=realized_pnl,
        mfe_pct=0.025,
        mae_pct=-0.005,
        meta={},
        parent_kind=parent_kind,  # type: ignore[arg-type]
    )


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def test_get_shadow_variants_for_trade_returns_variants_for_live_parent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live route: 2 variants returned + parent_kind serialized 'live'."""
    select_mock = AsyncMock(
        return_value=[
            _make_variant(row_id=1, parent_trade_id=7, parent_kind="live"),
            _make_variant(row_id=2, parent_trade_id=7, parent_kind="live"),
        ]
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.trades.select_shadow_variants_by_parent",
        select_mock,
    )
    response = client.get("/api/trades/7/shadow-variants")
    assert response.status_code == 200
    body = response.json()
    assert "variants" in body
    assert len(body["variants"]) == 2
    for v in body["variants"]:
        assert v["parent_kind"] == "live"
    # Decimal serialized as string per §5.3.
    assert body["variants"][0]["entry_price"] == "50000.00"
    assert body["variants"][0]["qty"] == "0.001"


def test_get_shadow_variants_for_paper_trade_returns_variants_for_paper_parent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paper route symmetric: parent_kind serialized 'paper'."""
    select_mock = AsyncMock(
        return_value=[
            _make_variant(row_id=10, parent_trade_id=42, parent_kind="paper"),
            _make_variant(row_id=11, parent_trade_id=42, parent_kind="paper"),
        ]
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_shadow_variants_by_parent",
        select_mock,
    )
    response = client.get("/api/paper-trades/42/shadow-variants")
    assert response.status_code == 200
    body = response.json()
    assert len(body["variants"]) == 2
    for v in body["variants"]:
        assert v["parent_kind"] == "paper"


def test_get_shadow_variants_returns_empty_list_when_no_variants_for_parent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent_trade_id with zero variants → 200 with empty list (NOT 404)."""
    select_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "services.analytics_api.app.routers.trades.select_shadow_variants_by_parent",
        select_mock,
    )
    response = client.get("/api/trades/999/shadow-variants")
    assert response.status_code == 200
    assert response.json() == {"variants": []}


def test_get_shadow_variants_router_passes_correct_parent_kind_kwarg(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parent_kind kwarg pin: live route → 'live'; paper route → 'paper' (ADR-0010 routing)."""
    live_mock = AsyncMock(return_value=[])
    paper_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "services.analytics_api.app.routers.trades.select_shadow_variants_by_parent",
        live_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.paper_trades.select_shadow_variants_by_parent",
        paper_mock,
    )
    client.get("/api/trades/7/shadow-variants")
    client.get("/api/paper-trades/42/shadow-variants")
    live_kwargs = _kwargs_of(live_mock)
    paper_kwargs = _kwargs_of(paper_mock)
    assert live_kwargs["parent_kind"] == "live"
    assert live_kwargs["parent_trade_id"] == 7
    assert paper_kwargs["parent_kind"] == "paper"
    assert paper_kwargs["parent_trade_id"] == 42


def test_get_shadow_variants_terminated_variant_serializes_outcome_and_pnl(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminated variant: terminal_outcome StrEnum value + realized_pnl as string."""
    select_mock = AsyncMock(
        return_value=[
            _make_variant(
                row_id=1,
                parent_trade_id=7,
                terminated_at=_T_TERMINATED,
                terminal_outcome=ShadowVariantTerminal.TP_FULL,
                realized_pnl=Decimal("15.75"),
            )
        ]
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.trades.select_shadow_variants_by_parent",
        select_mock,
    )
    response = client.get("/api/trades/7/shadow-variants")
    assert response.status_code == 200
    v = response.json()["variants"][0]
    assert v["terminal_outcome"] == "tp_full"  # StrEnum value (use_enum_values=True)
    assert v["realized_pnl"] == "15.75"
    assert v["terminated_at"] is not None
