"""Tests for ``/api/shadow/rejected/*`` read endpoints (T-517b1).

Mirror :mod:`services.analytics_api.tests.test_router_paper_trades` 1:1
modulo target table + filter set additions (terminal_outcome enum filter
NEW; status encodes ``terminated_at IS NULL/NOT NULL`` constant predicate).
All mocks at the router import boundary
(``services.analytics_api.app.routers.shadow_rejected``). Pin pagination
envelope shape (``rejected`` key per plan AC#5), Query validation
(ShadowRejectedTerminal StrEnum + Literal["active","terminated"] +
limit/offset bounds), enum-as-string serialization (``use_enum_values=True``
per plan AC#4), DOUBLE PRECISION as float, 404 detail format
``f'shadow_rejected {id} not found'`` (plan AC#8), and ``meta`` JSONB
passthrough (read-side via analytics-api lifespan codec per L-011).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from packages.core.types import ShadowRejectedTerminal
from packages.db.queries.shadow import ShadowRejectedRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_CREATED = datetime(2026, 5, 1, 10, 0, 0, tzinfo=UTC)
_T_TERMINATED = datetime(2026, 5, 1, 11, 0, 0, tzinfo=UTC)


def _make_rejected(
    *,
    rejected_id: int = 1,
    terminal_outcome: ShadowRejectedTerminal | None = ShadowRejectedTerminal.WOULD_TP,
    terminated_at: datetime | None = _T_TERMINATED,
    mfe_pct: float | None = 0.012,
    mae_pct: float | None = -0.003,
    meta: dict[str, object] | None = None,
) -> ShadowRejectedRow:
    return ShadowRejectedRow(
        id=rejected_id,
        signal_id=42,
        bot_id="alpha",
        symbol="BTCUSDT",
        would_side="buy",
        created_at=_T_CREATED,
        terminated_at=terminated_at,
        terminal_outcome=terminal_outcome,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        meta=meta if meta is not None else {},
    )


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    """Type-narrowed accessor for `mock.await_args.kwargs`."""
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def _patch_list(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[ShadowRejectedRow],
    total: int,
) -> tuple[AsyncMock, AsyncMock]:
    select_mock = AsyncMock(return_value=rows)
    count_mock = AsyncMock(return_value=total)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_rejected.select_shadow_rejected_paginated",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_rejected.count_shadow_rejected",
        count_mock,
    )
    return select_mock, count_mock


# ---------------------------------------------------------------------------
# LIST endpoint — envelope + pagination
# ---------------------------------------------------------------------------


def test_list_envelope_keys_and_default_pagination(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan AC#5 — envelope: rejected + total + limit + offset; defaults: limit=50, offset=0."""
    select_mock, count_mock = _patch_list(monkeypatch, rows=[_make_rejected()], total=1)
    response = client.get("/api/shadow/rejected/")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"rejected", "total", "limit", "offset"}
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["total"] == 1
    assert len(body["rejected"]) == 1
    select_kwargs = _kwargs_of(select_mock)
    assert select_kwargs["limit"] == 50
    assert select_kwargs["offset"] == 0
    count_mock.assert_awaited_once()


def test_list_pagination_query_params_are_forwarded(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/shadow/rejected/?limit=25&offset=100")
    kwargs = _kwargs_of(select_mock)
    assert kwargs["limit"] == 25
    assert kwargs["offset"] == 100


def test_list_rejects_limit_over_max(client: TestClient) -> None:
    """Query(le=200) → 422 on `?limit=999`."""
    response = client.get("/api/shadow/rejected/?limit=999")
    assert response.status_code == 422


def test_list_rejects_negative_offset(client: TestClient) -> None:
    """Query(ge=0) → 422 on `?offset=-1`."""
    response = client.get("/api/shadow/rejected/?offset=-1")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# LIST endpoint — filter validation + forwarding
# ---------------------------------------------------------------------------


def test_list_rejects_garbage_status_via_literal(client: TestClient) -> None:
    """`?status=garbage` → 422 (Literal['active', 'terminated'] validation)."""
    response = client.get("/api/shadow/rejected/?status=garbage")
    assert response.status_code == 422


def test_list_rejects_garbage_terminal_outcome_via_strenum(client: TestClient) -> None:
    """`?terminal_outcome=garbage` → 422 (ShadowRejectedTerminal StrEnum-validated)."""
    response = client.get("/api/shadow/rejected/?terminal_outcome=garbage")
    assert response.status_code == 422


def test_list_filter_bot_id_passes_to_helper(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/shadow/rejected/?bot_id=signabot1")
    assert _kwargs_of(select_mock)["bot_id"] == "signabot1"


def test_list_filter_symbol_and_status_active(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/shadow/rejected/?symbol=ETHUSDT&status=active")
    kwargs = _kwargs_of(select_mock)
    assert kwargs["symbol"] == "ETHUSDT"
    assert kwargs["status"] == "active"


def test_list_filter_terminal_outcome_passes_enum_value(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#1 — terminal_outcome value forwarded as the StrEnum instance (router boundary)."""
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get("/api/shadow/rejected/?terminal_outcome=would_tp")
    assert _kwargs_of(select_mock)["terminal_outcome"] is ShadowRejectedTerminal.WOULD_TP


def test_list_filter_from_to_iso8601_thread_to_helper(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`?from=` + `?to=` aliases parse to datetime + thread to query helpers (created_at range)."""
    select_mock, _ = _patch_list(monkeypatch, rows=[], total=0)
    client.get(
        "/api/shadow/rejected/?from=2026-05-01T00:00:00%2B00:00&to=2026-05-12T00:00:00%2B00:00",
    )
    kwargs = _kwargs_of(select_mock)
    assert kwargs["from_at"] == datetime(2026, 5, 1, tzinfo=UTC)
    assert kwargs["to_at"] == datetime(2026, 5, 12, tzinfo=UTC)


# ---------------------------------------------------------------------------
# DETAIL endpoint
# ---------------------------------------------------------------------------


def test_detail_returns_200_for_existing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_rejected.select_shadow_rejected_by_id",
        AsyncMock(return_value=_make_rejected(rejected_id=42)),
    )
    response = client.get("/api/shadow/rejected/42")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 42
    assert body["terminal_outcome"] == "would_tp"


def test_detail_returns_404_for_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan AC#8 — 404 detail format `f'shadow_rejected {id} not found'`."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_rejected.select_shadow_rejected_by_id",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/shadow/rejected/999")
    assert response.status_code == 404
    assert response.json()["detail"] == "shadow_rejected 999 not found"


# ---------------------------------------------------------------------------
# Serialization pins
# ---------------------------------------------------------------------------


def test_terminal_outcome_serialises_as_string_value(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan AC#4 — `model_config use_enum_values=True` → JSON string `"would_tp"`."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_rejected.select_shadow_rejected_by_id",
        AsyncMock(return_value=_make_rejected(terminal_outcome=ShadowRejectedTerminal.WOULD_TP)),
    )
    body = client.get("/api/shadow/rejected/1").json()
    assert body["terminal_outcome"] == "would_tp"
    assert isinstance(body["terminal_outcome"], str)


def test_double_precision_fields_serialise_as_float(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DOUBLE PRECISION columns (mfe_pct/mae_pct) → JSON numbers."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_rejected.select_shadow_rejected_by_id",
        AsyncMock(return_value=_make_rejected(mfe_pct=0.0123, mae_pct=-0.0045)),
    )
    body = client.get("/api/shadow/rejected/1").json()
    assert isinstance(body["mfe_pct"], float)
    assert isinstance(body["mae_pct"], float)
    assert body["mfe_pct"] == 0.0123
    assert body["mae_pct"] == -0.0045


def test_meta_jsonb_passthrough_returns_dict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`meta` JSONB renders as JSON object via analytics-api lifespan codec (L-011)."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_rejected.select_shadow_rejected_by_id",
        AsyncMock(return_value=_make_rejected(meta={"observation_window_min": 60, "tier": 1})),
    )
    body = client.get("/api/shadow/rejected/1").json()
    assert isinstance(body["meta"], dict)
    assert body["meta"] == {"observation_window_min": 60, "tier": 1}


def test_active_observation_returns_null_terminal_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active row: terminated_at / terminal_outcome / mfe_pct / mae_pct all null in response."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_rejected.select_shadow_rejected_by_id",
        AsyncMock(
            return_value=_make_rejected(
                terminal_outcome=None,
                terminated_at=None,
                mfe_pct=None,
                mae_pct=None,
            ),
        ),
    )
    body = client.get("/api/shadow/rejected/1").json()
    assert body["terminated_at"] is None
    assert body["terminal_outcome"] is None
    assert body["mfe_pct"] is None
    assert body["mae_pct"] is None
    assert body["would_side"] == "buy"  # would_side preserved
