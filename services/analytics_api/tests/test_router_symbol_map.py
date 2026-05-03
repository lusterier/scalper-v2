"""Tests for ``/api/symbol-map/*`` admin CRUD endpoints (T-401b).

All mocks at the router import boundary
(``services.analytics_api.app.routers.symbol_map``) per WG#8 from
T-401a's plan. Audit row writes via ``insert_audit_event`` mocked +
captured to assert the §16.8 atomic-write contract semantics:

* POST → action=symbol_map.create, before_state=None, after_state=row
* PUT  → action=symbol_map.update, before_state=pre, after_state=post
* DEL  → action=symbol_map.delete, before_state=pre, after_state=None

Also pin ``actor`` (``request.client.host``), ``correlation_id``
(``X-Correlation-ID`` header with empty/whitespace handling), and
``occurred_at`` (``app.state.now_fn`` injection per WG#7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from packages.core.types import ExchangeSource
from packages.db.queries.analytics import SymbolMapRow

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_T_CREATED = datetime(2026, 4, 20, 0, 0, 0, tzinfo=UTC)
_T_UPDATED = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
_FIXED_NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


def _audit_kwargs(audit_mock: AsyncMock) -> dict[str, Any]:
    """Type-narrowed accessor for `_audit_kwargs(audit_mock)`.

    `await_args` is typed as `_Call | None`; tests asserting kwargs need
    the narrow + readability over inline `assert ... is not None` chatter.
    """
    assert audit_mock.await_args is not None
    return dict(audit_mock.await_args.kwargs)


def await_aenter_value(mock_pool: Any) -> Any:
    """Return the ``fake_conn`` value yielded by ``pool.acquire().__aenter__()``.

    Used by WG#2 same-conn pins (assert helpers ran on one conn instance)
    + WG#4 transaction-not-opened pins (assert ``conn.transaction()``
    context manager was not entered when handler 404s pre-tx).
    """
    return mock_pool.acquire.return_value.__aenter__.return_value


def _make_sm_row(
    *,
    input_symbol: str = "BTCUSDT.P",
    canonical_symbol: str = "BTCUSDT",
    exchange_source: ExchangeSource = ExchangeSource.BINANCE,
    notes: str | None = None,
    created_at: datetime = _T_CREATED,
    updated_at: datetime = _T_UPDATED,
) -> SymbolMapRow:
    return SymbolMapRow(
        input_symbol=input_symbol,
        canonical_symbol=canonical_symbol,
        exchange_source=exchange_source,
        notes=notes,
        created_at=created_at,
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# READ endpoints
# ---------------------------------------------------------------------------


def test_list_symbol_map_returns_200_with_entries(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/symbol-map/ → 200 with 2 entries in caller order."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.select_all_symbol_map_entries",
        AsyncMock(
            return_value=[
                _make_sm_row(input_symbol="BTCUSDT.P", canonical_symbol="BTCUSDT"),
                _make_sm_row(
                    input_symbol="ETHUSDT.P",
                    canonical_symbol="ETHUSDT",
                    exchange_source=ExchangeSource.BYBIT,
                ),
            ]
        ),
    )
    response = client.get("/api/symbol-map/")
    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 2
    assert body["entries"][0]["input_symbol"] == "BTCUSDT.P"
    assert body["entries"][0]["exchange_source"] == "binance"
    assert body["entries"][1]["input_symbol"] == "ETHUSDT.P"
    assert body["entries"][1]["exchange_source"] == "bybit"


def test_list_symbol_map_returns_empty_envelope_when_no_rows(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.select_all_symbol_map_entries",
        AsyncMock(return_value=[]),
    )
    response = client.get("/api/symbol-map/")
    assert response.status_code == 200
    assert response.json() == {"entries": []}


def test_get_symbol_map_entry_returns_200_for_existing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.select_symbol_map_entry",
        AsyncMock(return_value=_make_sm_row(notes="hello")),
    )
    response = client.get("/api/symbol-map/BTCUSDT.P")
    assert response.status_code == 200
    body = response.json()
    assert body["input_symbol"] == "BTCUSDT.P"
    assert body["notes"] == "hello"


def test_get_symbol_map_entry_returns_404_for_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.select_symbol_map_entry",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/symbol-map/MISSING.P")
    assert response.status_code == 404
    assert "MISSING.P" in response.json()["detail"]


# ---------------------------------------------------------------------------
# WRITE endpoints — POST
# ---------------------------------------------------------------------------


def _patch_post_path(
    monkeypatch: pytest.MonkeyPatch,
    inserted_row: SymbolMapRow | None = None,
    *,
    insert_side_effect: BaseException | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    """Mock insert_symbol_map_entry + insert_audit_event; return both mocks."""
    if insert_side_effect is not None:
        insert_mock = AsyncMock(side_effect=insert_side_effect)
    else:
        insert_mock = AsyncMock(return_value=inserted_row)
    audit_mock = AsyncMock(return_value=42)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.insert_symbol_map_entry",
        insert_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.insert_audit_event",
        audit_mock,
    )
    return insert_mock, audit_mock


def test_post_symbol_map_returns_201_and_writes_audit_row(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/symbol-map/ → 201 + atomic audit row write."""
    inserted = _make_sm_row(
        input_symbol="LTCUSDT.P",
        canonical_symbol="LTCUSDT",
        notes="first listing",
    )
    insert_mock, audit_mock = _patch_post_path(monkeypatch, inserted_row=inserted)

    response = client.post(
        "/api/symbol-map/",
        json={
            "input_symbol": "LTCUSDT.P",
            "canonical_symbol": "LTCUSDT",
            "exchange_source": "binance",
            "notes": "first listing",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["input_symbol"] == "LTCUSDT.P"

    insert_mock.assert_awaited_once()
    audit_mock.assert_awaited_once()
    audit_kwargs = _audit_kwargs(audit_mock)
    assert audit_kwargs["action"] == "symbol_map.create"
    assert audit_kwargs["entity_type"] == "symbol_map"
    assert audit_kwargs["entity_id"] == "LTCUSDT.P"
    assert audit_kwargs["before_state"] is None
    assert audit_kwargs["after_state"]["input_symbol"] == "LTCUSDT.P"
    assert audit_kwargs["after_state"]["canonical_symbol"] == "LTCUSDT"


def test_post_symbol_map_returns_409_on_unique_violation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST → 409 on duplicate; audit row NOT written (rollback)."""
    insert_mock, audit_mock = _patch_post_path(
        monkeypatch,
        insert_side_effect=asyncpg.UniqueViolationError("duplicate key"),
    )
    response = client.post(
        "/api/symbol-map/",
        json={
            "input_symbol": "BTCUSDT.P",
            "canonical_symbol": "BTCUSDT",
            "exchange_source": "binance",
            "notes": None,
        },
    )
    assert response.status_code == 409
    assert "BTCUSDT.P" in response.json()["detail"]
    insert_mock.assert_awaited_once()
    audit_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# WRITE endpoints — PUT
# ---------------------------------------------------------------------------


def _patch_put_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pre_read_row: SymbolMapRow | None,
    updated_row: SymbolMapRow | None = None,
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Mock select + update + audit; return all three."""
    select_mock = AsyncMock(return_value=pre_read_row)
    update_mock = AsyncMock(return_value=updated_row)
    audit_mock = AsyncMock(return_value=43)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.select_symbol_map_entry",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.update_symbol_map_entry",
        update_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.insert_audit_event",
        audit_mock,
    )
    return select_mock, update_mock, audit_mock


def test_put_symbol_map_returns_200_and_writes_audit_row(
    client: TestClient,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT → 200; before_state from pre-read, after_state from post-update.

    WG#2 also pinned here: select / update / insert_audit_event MUST all
    be called on the SAME ``conn`` instance (single ``pool.acquire()``);
    splitting into two acquires would break atomicity. Asserted via
    ``await_args.args[0] is mock_conn`` for each of the 3 helper calls.
    """
    pre = _make_sm_row(canonical_symbol="BTCUSDT", notes=None)
    post = _make_sm_row(canonical_symbol="BTCUSDT_NEW", notes="renamed")
    select_mock, update_mock, audit_mock = _patch_put_path(
        monkeypatch,
        pre_read_row=pre,
        updated_row=post,
    )

    response = client.put(
        "/api/symbol-map/BTCUSDT.P",
        json={
            "canonical_symbol": "BTCUSDT_NEW",
            "exchange_source": "binance",
            "notes": "renamed",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_symbol"] == "BTCUSDT_NEW"

    update_mock.assert_awaited_once()
    audit_mock.assert_awaited_once()
    audit_kwargs = _audit_kwargs(audit_mock)
    assert audit_kwargs["action"] == "symbol_map.update"
    assert audit_kwargs["entity_id"] == "BTCUSDT.P"
    assert audit_kwargs["before_state"]["canonical_symbol"] == "BTCUSDT"
    assert audit_kwargs["after_state"]["canonical_symbol"] == "BTCUSDT_NEW"

    # WG#2: same-conn pin — pre-read + update + audit emit MUST share one conn.
    expected_conn = await_aenter_value(mock_pool)
    assert select_mock.await_args is not None
    assert update_mock.await_args is not None
    assert audit_mock.await_args is not None
    assert select_mock.await_args.args[0] is expected_conn
    assert update_mock.await_args.args[0] is expected_conn
    assert audit_mock.await_args.args[0] is expected_conn


def test_put_symbol_map_returns_404_when_missing(
    client: TestClient,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT pre-read returns None → 404 BEFORE tx; audit + update NOT called.

    WG#4 also pinned: ``conn.transaction()`` context manager MUST NOT
    have been entered (no empty tx open + immediate rollback).
    """
    _, update_mock, audit_mock = _patch_put_path(monkeypatch, pre_read_row=None)
    fake_conn = await_aenter_value(mock_pool)
    response = client.put(
        "/api/symbol-map/MISSING.P",
        json={
            "canonical_symbol": "X",
            "exchange_source": "custom",
            "notes": None,
        },
    )
    assert response.status_code == 404
    update_mock.assert_not_awaited()
    audit_mock.assert_not_awaited()
    # WG#4: transaction context-manager .__aenter__ never invoked.
    fake_conn.transaction.return_value.__aenter__.assert_not_called()


# ---------------------------------------------------------------------------
# WRITE endpoints — DELETE
# ---------------------------------------------------------------------------


def _patch_delete_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pre_read_row: SymbolMapRow | None,
    delete_returns: bool = True,
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    select_mock = AsyncMock(return_value=pre_read_row)
    delete_mock = AsyncMock(return_value=delete_returns)
    audit_mock = AsyncMock(return_value=44)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.select_symbol_map_entry",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.delete_symbol_map_entry",
        delete_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.insert_audit_event",
        audit_mock,
    )
    return select_mock, delete_mock, audit_mock


def test_delete_symbol_map_returns_204_and_writes_audit_row(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre = _make_sm_row(canonical_symbol="BTCUSDT")
    _, delete_mock, audit_mock = _patch_delete_path(monkeypatch, pre_read_row=pre)

    response = client.delete("/api/symbol-map/BTCUSDT.P")
    assert response.status_code == 204
    delete_mock.assert_awaited_once()
    audit_mock.assert_awaited_once()
    audit_kwargs = _audit_kwargs(audit_mock)
    assert audit_kwargs["action"] == "symbol_map.delete"
    assert audit_kwargs["entity_id"] == "BTCUSDT.P"
    assert audit_kwargs["before_state"]["canonical_symbol"] == "BTCUSDT"
    assert audit_kwargs["after_state"] is None


def test_delete_symbol_map_returns_404_when_missing(
    client: TestClient,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE pre-read returns None → 404 BEFORE tx; same WG#4 contract as PUT."""
    _, delete_mock, audit_mock = _patch_delete_path(monkeypatch, pre_read_row=None)
    fake_conn = await_aenter_value(mock_pool)
    response = client.delete("/api/symbol-map/MISSING.P")
    assert response.status_code == 404
    delete_mock.assert_not_awaited()
    audit_mock.assert_not_awaited()
    # WG#4: transaction context-manager .__aenter__ never invoked.
    fake_conn.transaction.return_value.__aenter__.assert_not_called()


# ---------------------------------------------------------------------------
# Audit metadata pins (actor + correlation_id + now_fn)
# ---------------------------------------------------------------------------


def test_audit_actor_derived_from_request_client_host(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``actor='lan:<host>'`` per §16.8:2227; TestClient default host = 'testclient'."""
    inserted = _make_sm_row(input_symbol="X.P", canonical_symbol="X")
    _, audit_mock = _patch_post_path(monkeypatch, inserted_row=inserted)
    client.post(
        "/api/symbol-map/",
        json={
            "input_symbol": "X.P",
            "canonical_symbol": "X",
            "exchange_source": "binance",
            "notes": None,
        },
    )
    assert _audit_kwargs(audit_mock)["actor"] == "lan:testclient"


def test_audit_correlation_id_from_request_header(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``X-Correlation-ID`` header threads through to audit row."""
    inserted = _make_sm_row(input_symbol="X.P", canonical_symbol="X")
    _, audit_mock = _patch_post_path(monkeypatch, inserted_row=inserted)
    client.post(
        "/api/symbol-map/",
        json={
            "input_symbol": "X.P",
            "canonical_symbol": "X",
            "exchange_source": "binance",
            "notes": None,
        },
        headers={"X-Correlation-ID": "cid-from-header-123"},
    )
    assert _audit_kwargs(audit_mock)["correlation_id"] == "cid-from-header-123"


@pytest.mark.parametrize("header_value", [None, "", "   ", "\t\n"])
def test_audit_correlation_id_none_when_header_missing_or_empty(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    header_value: str | None,
) -> None:
    """Missing / empty / whitespace-only ``X-Correlation-ID`` → audit ``correlation_id=None``."""
    inserted = _make_sm_row(input_symbol="X.P", canonical_symbol="X")
    _, audit_mock = _patch_post_path(monkeypatch, inserted_row=inserted)
    headers = {} if header_value is None else {"X-Correlation-ID": header_value}
    client.post(
        "/api/symbol-map/",
        json={
            "input_symbol": "X.P",
            "canonical_symbol": "X",
            "exchange_source": "binance",
            "notes": None,
        },
        headers=headers,
    )
    assert _audit_kwargs(audit_mock)["correlation_id"] is None


def test_audit_now_fn_used_for_occurred_at(
    app_with_mocks: Any,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#7 — patch ``app.state.now_fn`` directly; audit ``occurred_at`` == FIXED_NOW."""
    inserted = _make_sm_row(input_symbol="X.P", canonical_symbol="X")
    _, audit_mock = _patch_post_path(monkeypatch, inserted_row=inserted)
    app_with_mocks.state.now_fn = lambda: _FIXED_NOW

    client.post(
        "/api/symbol-map/",
        json={
            "input_symbol": "X.P",
            "canonical_symbol": "X",
            "exchange_source": "binance",
            "notes": None,
        },
    )
    assert _audit_kwargs(audit_mock)["occurred_at"] == _FIXED_NOW


def test_audit_entity_id_uses_url_path_value_not_body(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#10 — PUT/DELETE entity_id derives from URL path (body lacks input_symbol)."""
    pre = _make_sm_row(input_symbol="PATH.P", canonical_symbol="OLD")
    post = _make_sm_row(input_symbol="PATH.P", canonical_symbol="NEW")
    _, _, audit_mock = _patch_put_path(
        monkeypatch,
        pre_read_row=pre,
        updated_row=post,
    )

    client.put(
        "/api/symbol-map/PATH.P",
        json={
            "canonical_symbol": "NEW",
            "exchange_source": "binance",
            "notes": None,
        },
    )
    assert _audit_kwargs(audit_mock)["entity_id"] == "PATH.P"


def test_audit_failure_propagates_5xx_when_audit_insert_raises(
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit insert raises mid-tx → router does NOT swallow; exception surfaces.

    Uses a fresh TestClient with ``raise_server_exceptions=False`` so the
    error materialises as a 500 response (Starlette default re-raises in
    test harness for debuggability). Pinning the contract: router does
    not catch ``insert_audit_event`` exceptions — they propagate so
    asyncpg auto-rolls-back the business mutation.
    """
    from fastapi.testclient import TestClient as _TestClient

    inserted = _make_sm_row(input_symbol="X.P", canonical_symbol="X")
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.insert_symbol_map_entry",
        AsyncMock(return_value=inserted),
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.insert_audit_event",
        AsyncMock(side_effect=RuntimeError("audit failure simulated")),
    )
    with _TestClient(app_with_mocks, raise_server_exceptions=False) as raw_client:
        response = raw_client.post(
            "/api/symbol-map/",
            json={
                "input_symbol": "X.P",
                "canonical_symbol": "X",
                "exchange_source": "binance",
                "notes": None,
            },
        )
    assert response.status_code >= 500


# ---------------------------------------------------------------------------
# WG#9 — structured-log emission post-commit (NOT inside tx)
# ---------------------------------------------------------------------------


def test_post_symbol_map_emits_system_log_event_after_commit(
    app_with_mocks: Any,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#9 — `symbol_map.created` log event emitted AFTER tx commits successfully.

    Pin via mock-replace `app.state.logger` and assert .info() called with
    the action string + correct kwargs after a successful POST.
    """
    inserted = _make_sm_row(input_symbol="X.P", canonical_symbol="X")
    _patch_post_path(monkeypatch, inserted_row=inserted)
    logger_mock = MagicMock()
    app_with_mocks.state.logger = logger_mock

    response = client.post(
        "/api/symbol-map/",
        json={
            "input_symbol": "X.P",
            "canonical_symbol": "X",
            "exchange_source": "binance",
            "notes": None,
        },
    )
    assert response.status_code == 201
    logger_mock.info.assert_called_once()
    call_args = logger_mock.info.call_args
    assert call_args.args[0] == "symbol_map.create"
    assert call_args.kwargs["input_symbol"] == "X.P"
    assert call_args.kwargs["actor"] == "lan:testclient"


def test_log_NOT_emitted_when_audit_fails_inside_tx(
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#9 — log event MUST NOT emit if audit insert raises (rollback path).

    If a future regression moved the logger.info call INSIDE the
    `async with conn.transaction():` block, the log would fire before
    the rollback materialised + leave a misleading "created" event in
    log streams. This test pins logger NOT called when audit fails.
    """
    from fastapi.testclient import TestClient as _TestClient

    inserted = _make_sm_row(input_symbol="X.P", canonical_symbol="X")
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.insert_symbol_map_entry",
        AsyncMock(return_value=inserted),
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.symbol_map.insert_audit_event",
        AsyncMock(side_effect=RuntimeError("audit failure simulated")),
    )
    logger_mock = MagicMock()
    app_with_mocks.state.logger = logger_mock

    with _TestClient(app_with_mocks, raise_server_exceptions=False) as raw_client:
        raw_client.post(
            "/api/symbol-map/",
            json={
                "input_symbol": "X.P",
                "canonical_symbol": "X",
                "exchange_source": "binance",
                "notes": None,
            },
        )
    # logger.info should NOT have been called for symbol_map.create — log
    # call sits AFTER the `async with conn.transaction():` block; tx
    # rollback (via raised exception) skips the post-commit log emit.
    create_calls = [
        c for c in logger_mock.info.call_args_list if c.args and c.args[0] == "symbol_map.create"
    ]
    assert create_calls == []
