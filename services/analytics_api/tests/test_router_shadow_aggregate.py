"""Tests for ``/api/shadow/aggregate/{symbol}`` aggregate endpoint (T-517a1).

Mirror :mod:`services.analytics_api.tests.test_router_shadow_rejected` mock
pattern modulo path-param routing + aggregate-specific envelope shape. All
mocks at the router import boundary
(``services.analytics_api.app.routers.shadow_aggregate``).

Pin envelope keys (`symbol/variants/bot_id/from_at/to_at`), Query alias
forwarding, fetch-then-compute orchestration (compute called with select
result), Decimal-as-string + DOUBLE PRECISION serialization, empty result
→ 200 (not 404), and sort order pin via mock fixture.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from packages.db.queries.shadow import ShadowVariantAggregateRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_NOW = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


def _make_row(
    *,
    variant_name: str = "conservative",
    realized_pnl: str = "5.00",
    mfe_pct: float | None = 0.012,
    mae_pct: float | None = -0.005,
    parent_kind: str = "live",
) -> ShadowVariantAggregateRow:
    return ShadowVariantAggregateRow(
        parent_symbol="BTCUSDT",
        bot_id="alpha",
        variant_name=variant_name,
        realized_pnl=Decimal(realized_pnl),
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        parent_kind=parent_kind,  # type: ignore[arg-type]
        created_at=_T_NOW,
    )


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    """Type-narrowed accessor for `mock.await_args.kwargs`."""
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def _patch_select(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[ShadowVariantAggregateRow],
) -> AsyncMock:
    select_mock = AsyncMock(return_value=rows)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.shadow_aggregate.select_shadow_variants_for_aggregate",
        select_mock,
    )
    return select_mock


# ---------------------------------------------------------------------------
# Envelope + path param + filter forwarding
# ---------------------------------------------------------------------------


def test_get_aggregate_envelope_keys(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan AC#13 — envelope: symbol + variants + bot_id + from_at + to_at."""
    _patch_select(monkeypatch, rows=[_make_row()])
    response = client.get("/api/shadow/aggregate/BTCUSDT")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"symbol", "variants", "bot_id", "from_at", "to_at"}
    assert body["symbol"] == "BTCUSDT"


def test_get_aggregate_path_param_forwards_to_helper(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock = _patch_select(monkeypatch, rows=[])
    client.get("/api/shadow/aggregate/ETHUSDT")
    assert _kwargs_of(select_mock)["symbol"] == "ETHUSDT"


def test_get_aggregate_bot_id_filter_forwards(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock = _patch_select(monkeypatch, rows=[])
    client.get("/api/shadow/aggregate/BTCUSDT?bot_id=alpha")
    assert _kwargs_of(select_mock)["bot_id"] == "alpha"


def test_get_aggregate_from_to_filter_forwards(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`?from=` + `?to=` aliases parse to datetime + thread to query helpers."""
    select_mock = _patch_select(monkeypatch, rows=[])
    client.get(
        "/api/shadow/aggregate/BTCUSDT"
        "?from=2026-05-01T00:00:00%2B00:00&to=2026-05-12T00:00:00%2B00:00",
    )
    kwargs = _kwargs_of(select_mock)
    assert kwargs["from_at"] == datetime(2026, 5, 1, tzinfo=UTC)
    assert kwargs["to_at"] == datetime(2026, 5, 12, tzinfo=UTC)


def test_get_aggregate_default_filters_are_None(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No query params → kwargs bot_id=None, from_at=None, to_at=None."""
    select_mock = _patch_select(monkeypatch, rows=[])
    client.get("/api/shadow/aggregate/BTCUSDT")
    kwargs = _kwargs_of(select_mock)
    assert kwargs["bot_id"] is None
    assert kwargs["from_at"] is None
    assert kwargs["to_at"] is None


# ---------------------------------------------------------------------------
# Empty result + serialization + orchestration
# ---------------------------------------------------------------------------


def test_get_aggregate_empty_result_returns_200_with_empty_variants(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan AC#16 — empty result → 200 with variants=[] (NOT 404)."""
    _patch_select(monkeypatch, rows=[])
    response = client.get("/api/shadow/aggregate/BTCUSDT")
    assert response.status_code == 200
    body = response.json()
    assert body["variants"] == []
    assert body["symbol"] == "BTCUSDT"


def test_get_aggregate_decimal_fields_serialize_as_string(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan AC#10 — total_pnl/avg_pnl/best_pnl/worst_pnl as JSON strings (§5.3)."""
    _patch_select(
        monkeypatch,
        rows=[
            _make_row(variant_name="conservative", realized_pnl="12.50"),
            _make_row(variant_name="conservative", realized_pnl="-3.25"),
        ],
    )
    body = client.get("/api/shadow/aggregate/BTCUSDT").json()
    variant = body["variants"][0]
    assert isinstance(variant["total_pnl"], str)
    assert isinstance(variant["avg_pnl"], str)
    assert isinstance(variant["best_pnl"], str)
    assert isinstance(variant["worst_pnl"], str)
    # 12.50 + -3.25 = 9.25
    assert variant["total_pnl"] == "9.25"


def test_get_aggregate_double_precision_fields_serialize_as_float(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """win_rate / avg_mfe_pct / avg_mae_pct → JSON numbers."""
    _patch_select(
        monkeypatch,
        rows=[
            _make_row(variant_name="x", realized_pnl="10", mfe_pct=0.020, mae_pct=-0.005),
            _make_row(variant_name="x", realized_pnl="-5", mfe_pct=0.010, mae_pct=-0.015),
        ],
    )
    body = client.get("/api/shadow/aggregate/BTCUSDT").json()
    variant = body["variants"][0]
    assert isinstance(variant["win_rate"], float)
    assert isinstance(variant["avg_mfe_pct"], float)
    assert isinstance(variant["avg_mae_pct"], float)
    assert variant["win_rate"] == 0.5  # 1 win / 2 trades
    assert variant["avg_mfe_pct"] == 0.015  # (0.020 + 0.010) / 2


def test_get_aggregate_invokes_compute_and_returns_metrics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wire-up sanity: select rows → compute_variant_aggregate produces variants."""
    select_mock = _patch_select(
        monkeypatch,
        rows=[
            _make_row(variant_name="x", realized_pnl="5"),
            _make_row(variant_name="x", realized_pnl="3"),
        ],
    )
    body = client.get("/api/shadow/aggregate/BTCUSDT").json()
    select_mock.assert_awaited_once()
    assert len(body["variants"]) == 1
    assert body["variants"][0]["variant_name"] == "x"
    assert body["variants"][0]["n_trades"] == 2


def test_get_aggregate_variants_sorted_by_total_pnl_desc_tiebreak_name_asc(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#2 — output order pin: aggressive (a < n) before no_be when tied at total_pnl."""
    _patch_select(
        monkeypatch,
        rows=[
            # 3 variants: 2 tied at total_pnl=20 (different names), 1 at total_pnl=10.
            _make_row(variant_name="no_be", realized_pnl="20"),
            _make_row(variant_name="aggressive", realized_pnl="20"),
            _make_row(variant_name="conservative", realized_pnl="10"),
        ],
    )
    body = client.get("/api/shadow/aggregate/BTCUSDT").json()
    names = [v["variant_name"] for v in body["variants"]]
    # Tie-break: aggressive < no_be alphabetically.
    assert names == ["aggressive", "no_be", "conservative"]
