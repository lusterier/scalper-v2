"""Tests for ``/api/configs/*`` read+write endpoints (T-405).

Mocks at the router import boundary
(``services.analytics_api.app.routers.configs``) per WG#15 + T-401b
precedent. Pin: validate-before-tx ordering (WG#1), 5-helper same-conn
pin (WG#2), concurrent race → 409 (WG#3), update_bots False → 5xx
(WG#9), audit before/after exclude config_yaml (WG#10), bot_id mismatch
→ 409 (WG#11), config_hash raw-bytes determinism (WG#8), validate
parsed_version=None on failure (WG#7).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import asyncpg

from packages.db.queries.analytics import BotConfigRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_APPLIED = datetime(2026, 5, 3, 12, 0, 0, tzinfo=UTC)
_FIXED_NOW = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def await_aenter_value(mock_pool: Any) -> Any:
    return mock_pool.acquire.return_value.__aenter__.return_value


def _make_bc_row(
    *,
    config_id: int = 1,
    bot_id: str = "alpha",
    version: int = 1,
    notes: str | None = None,
    config_yaml: str | None = None,
) -> BotConfigRow:
    return BotConfigRow(
        id=config_id,
        bot_id=bot_id,
        version=version,
        applied_at=_T_APPLIED,
        applied_by="operator",
        config_yaml=config_yaml or "bot_id: alpha\n",
        config_hash="deadbeef" * 8,
        notes=notes,
    )


# Minimal valid YAML body — alpha bot with paper exchange + dummy execution.
_VALID_YAML_ALPHA = """\
bot_id: alpha
trading: { universe: [BTCUSDT] }
scoring:
  mode: passthrough
  trigger_threshold: 0.0
  rules: []
exchange:
  mode: paper
  account: sub_alpha
  api_key_env: BOT_ALPHA_BYBIT_API_KEY
  api_secret_env: BOT_ALPHA_BYBIT_API_SECRET
execution:
  qty: 0.001
  leverage: 20
  sl_pct: 0.01
  tp_pct: 0.01
  tp_qty_pct: 0.5
  be_trigger: 0.005
  be_sl_level: 0.003
  trail_pct: 0.005
  fee_rate: 0.00055
"""


def _patch_apply_helpers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    before_row: BotConfigRow | None = None,
    max_version: int = 0,
    inserted_row: BotConfigRow | None = None,
    insert_side_effect: BaseException | None = None,
    update_returns: bool = True,
) -> tuple[AsyncMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    """Mock 5 apply helpers; return all 5 mocks for same-conn assertions."""
    select_current = AsyncMock(return_value=before_row)
    select_max = AsyncMock(return_value=max_version)
    if insert_side_effect is not None:
        insert = AsyncMock(side_effect=insert_side_effect)
    else:
        insert = AsyncMock(return_value=inserted_row)
    update = AsyncMock(return_value=update_returns)
    audit = AsyncMock(return_value=42)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.select_bot_config_current",
        select_current,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.select_max_bot_config_version",
        select_max,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.insert_bot_config",
        insert,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.update_bot_config_applied",
        update,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.insert_audit_event",
        audit,
    )
    return select_current, select_max, insert, update, audit


# ---------------------------------------------------------------------------
# READ endpoints
# ---------------------------------------------------------------------------


def test_get_current_bot_config_returns_200_for_existing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.select_bot_config_current",
        AsyncMock(return_value=_make_bc_row(version=3)),
    )
    response = client.get("/api/configs/alpha")
    assert response.status_code == 200
    assert response.json()["version"] == 3


def test_get_current_bot_config_returns_404_when_no_versions(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.select_bot_config_current",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/configs/missing")
    assert response.status_code == 404
    assert "missing" in response.json()["detail"]


def test_list_bot_config_versions_paginated(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.select_bot_config_versions",
        AsyncMock(
            return_value=[_make_bc_row(version=3), _make_bc_row(version=2)],
        ),
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.count_bot_config_versions",
        AsyncMock(return_value=3),
    )
    body = client.get("/api/configs/alpha/versions?limit=10&offset=0").json()
    assert set(body.keys()) == {"versions", "total", "limit", "offset"}
    assert body["limit"] == 10
    assert body["total"] == 3
    assert len(body["versions"]) == 2


def test_get_bot_config_version_returns_200_for_existing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.select_bot_config_by_version",
        AsyncMock(return_value=_make_bc_row(version=2)),
    )
    response = client.get("/api/configs/alpha/versions/2")
    assert response.status_code == 200
    assert response.json()["version"] == 2


def test_get_bot_config_version_returns_404_for_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.configs.select_bot_config_by_version",
        AsyncMock(return_value=None),
    )
    response = client.get("/api/configs/alpha/versions/99")
    assert response.status_code == 404
    assert "version 99" in response.json()["detail"]


# ---------------------------------------------------------------------------
# VALIDATE endpoint
# ---------------------------------------------------------------------------


def test_post_validate_returns_valid_true_for_correct_yaml(client: TestClient) -> None:
    response = client.post(
        "/api/configs/validate",
        json={"bot_id": "alpha", "yaml_text": _VALID_YAML_ALPHA},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["bot_id"] == "alpha"
    assert body["parsed_version"] == 1
    assert body["errors"] == []


def test_post_validate_returns_valid_false_with_errors_on_malformed_yaml(
    client: TestClient,
) -> None:
    """WG#7 — parsed_version is ALWAYS None when valid=False; errors non-empty."""
    response = client.post(
        "/api/configs/validate",
        json={"bot_id": "alpha", "yaml_text": "- not a mapping\n"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["parsed_version"] is None
    assert len(body["errors"]) >= 1


def test_post_validate_rejects_bad_request_body_shape(client: TestClient) -> None:
    """422 reserved for malformed Pydantic body shape (not validation failure)."""
    response = client.post("/api/configs/validate", json={"bot_id": ""})  # missing yaml_text
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# APPLY endpoint
# ---------------------------------------------------------------------------


def test_post_apply_returns_201_and_writes_audit_row(
    client: TestClient,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#2 — ALL 5 helpers called once on same conn (apply happy path)."""
    inserted = _make_bc_row(version=1, config_yaml=_VALID_YAML_ALPHA)
    select_current, select_max, insert, update, audit = _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        inserted_row=inserted,
    )

    response = client.post(
        "/api/configs/alpha/apply",
        json={
            "yaml_text": _VALID_YAML_ALPHA,
            "applied_by": "operator",
            "notes": "first apply",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["bot_id"] == "alpha"
    assert body["version"] == 1

    # All 5 helpers awaited exactly once.
    select_current.assert_awaited_once()
    select_max.assert_awaited_once()
    insert.assert_awaited_once()
    update.assert_awaited_once()
    audit.assert_awaited_once()
    # All 5 helpers ran on the SAME conn instance per WG#2.
    expected_conn = await_aenter_value(mock_pool)
    for mock in (select_current, select_max, insert, update, audit):
        assert mock.await_args is not None
        assert mock.await_args.args[0] is expected_conn


def test_post_apply_yaml_validation_failure_returns_422_before_tx(
    client: TestClient,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#1 — YAML validation failure → 422 BEFORE conn.transaction() opens."""
    select_current, _, insert, _, audit = _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        inserted_row=_make_bc_row(),
    )
    fake_conn = await_aenter_value(mock_pool)
    response = client.post(
        "/api/configs/alpha/apply",
        json={
            "yaml_text": "- not a mapping\n",
            "applied_by": "operator",
            "notes": None,
        },
    )
    assert response.status_code == 422
    # tx never opened, helpers never called.
    select_current.assert_not_awaited()
    insert.assert_not_awaited()
    audit.assert_not_awaited()
    fake_conn.transaction.return_value.__aenter__.assert_not_called()


def test_post_apply_returns_409_on_bot_id_url_body_mismatch(
    client: TestClient,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#11 — URL bot_id != YAML body bot_id → 409 BEFORE tx opens."""
    select_current, _, insert, _, audit = _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        inserted_row=_make_bc_row(),
    )
    fake_conn = await_aenter_value(mock_pool)
    response = client.post(
        "/api/configs/beta/apply",  # URL says beta
        json={
            "yaml_text": _VALID_YAML_ALPHA,  # body says alpha
            "applied_by": "operator",
            "notes": None,
        },
    )
    assert response.status_code == 409
    assert "mismatch" in response.json()["detail"]
    select_current.assert_not_awaited()
    insert.assert_not_awaited()
    audit.assert_not_awaited()
    fake_conn.transaction.return_value.__aenter__.assert_not_called()


def test_post_apply_concurrent_race_returns_409(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#3 — concurrent race detected via UniqueViolationError → 409 (NOT prevented)."""
    _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        insert_side_effect=asyncpg.UniqueViolationError("(bot_id, version) collision"),
    )
    response = client.post(
        "/api/configs/alpha/apply",
        json={
            "yaml_text": _VALID_YAML_ALPHA,
            "applied_by": "operator",
            "notes": None,
        },
    )
    assert response.status_code == 409
    assert "race" in response.json()["detail"]


def test_post_apply_audit_before_state_is_current_row_or_none(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First apply → before_state=None; subsequent → before_state=current row dict."""
    _, _, _, _, audit_first = _patch_apply_helpers(
        monkeypatch,
        before_row=None,  # first apply
        max_version=0,
        inserted_row=_make_bc_row(version=1, config_yaml=_VALID_YAML_ALPHA),
    )
    client.post(
        "/api/configs/alpha/apply",
        json={"yaml_text": _VALID_YAML_ALPHA, "applied_by": "operator", "notes": None},
    )
    assert _kwargs_of(audit_first)["before_state"] is None

    # Subsequent apply: before_state is a dict.
    prev_row = _make_bc_row(version=1, config_yaml=_VALID_YAML_ALPHA)
    _, _, _, _, audit_second = _patch_apply_helpers(
        monkeypatch,
        before_row=prev_row,
        max_version=1,
        inserted_row=_make_bc_row(version=2, config_yaml=_VALID_YAML_ALPHA),
    )
    client.post(
        "/api/configs/alpha/apply",
        json={"yaml_text": _VALID_YAML_ALPHA, "applied_by": "operator", "notes": None},
    )
    before = _kwargs_of(audit_second)["before_state"]
    assert isinstance(before, dict)
    assert before["version"] == 1


def test_apply_audit_states_exclude_config_yaml_to_avoid_jsonb_bloat(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#10 — audit before_state + after_state both EXCLUDE config_yaml field (7-key shape)."""
    prev_row = _make_bc_row(version=1, config_yaml=_VALID_YAML_ALPHA)
    inserted = _make_bc_row(version=2, config_yaml=_VALID_YAML_ALPHA)
    _, _, _, _, audit = _patch_apply_helpers(
        monkeypatch,
        before_row=prev_row,
        max_version=1,
        inserted_row=inserted,
    )
    client.post(
        "/api/configs/alpha/apply",
        json={"yaml_text": _VALID_YAML_ALPHA, "applied_by": "operator", "notes": None},
    )
    kwargs = _kwargs_of(audit)
    before = kwargs["before_state"]
    after = kwargs["after_state"]
    assert isinstance(before, dict)
    assert isinstance(after, dict)
    assert "config_yaml" not in before
    assert "config_yaml" not in after
    # Both should have the 7 non-yaml fields.
    expected_keys = {
        "id",
        "bot_id",
        "version",
        "applied_at",
        "applied_by",
        "config_hash",
        "notes",
    }
    assert set(before.keys()) == expected_keys
    assert set(after.keys()) == expected_keys


def test_post_apply_config_hash_is_sha256_of_raw_yaml_bytes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8 — config_hash = sha256(yaml_text.encode('utf-8')); raw bytes, no .strip()."""
    inserted = _make_bc_row(version=1, config_yaml=_VALID_YAML_ALPHA)
    _, _, insert, _, _ = _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        inserted_row=inserted,
    )
    client.post(
        "/api/configs/alpha/apply",
        json={"yaml_text": _VALID_YAML_ALPHA, "applied_by": "operator", "notes": None},
    )
    expected_hash = hashlib.sha256(_VALID_YAML_ALPHA.encode("utf-8")).hexdigest()
    assert _kwargs_of(insert)["config_hash"] == expected_hash


def test_post_apply_config_hash_changes_with_trailing_newline(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8 — raw-bytes policy: trailing whitespace CHANGES hash (no .strip())."""
    yaml_with_extra_newline = _VALID_YAML_ALPHA + "\n"
    inserted = _make_bc_row(version=1, config_yaml=yaml_with_extra_newline)
    _, _, insert, _, _ = _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        inserted_row=inserted,
    )
    client.post(
        "/api/configs/alpha/apply",
        json={"yaml_text": yaml_with_extra_newline, "applied_by": "operator", "notes": None},
    )
    hash_with_newline = hashlib.sha256(yaml_with_extra_newline.encode("utf-8")).hexdigest()
    hash_without_newline = hashlib.sha256(_VALID_YAML_ALPHA.encode("utf-8")).hexdigest()
    assert hash_with_newline != hash_without_newline
    assert _kwargs_of(insert)["config_hash"] == hash_with_newline


def test_post_apply_increments_version(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """select_max returns 3 → insert called with version=4."""
    inserted = _make_bc_row(version=4, config_yaml=_VALID_YAML_ALPHA)
    _, _, insert, _, _ = _patch_apply_helpers(
        monkeypatch,
        before_row=_make_bc_row(version=3, config_yaml=_VALID_YAML_ALPHA),
        max_version=3,
        inserted_row=inserted,
    )
    client.post(
        "/api/configs/alpha/apply",
        json={"yaml_text": _VALID_YAML_ALPHA, "applied_by": "operator", "notes": None},
    )
    assert _kwargs_of(insert)["version"] == 4


def test_apply_raises_5xx_when_update_bots_returns_false(
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#9 — update_bot_config_applied returns False → RuntimeError → tx rollback → 5xx."""
    from fastapi.testclient import TestClient as _TestClient

    inserted = _make_bc_row(version=1, config_yaml=_VALID_YAML_ALPHA)
    _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        inserted_row=inserted,
        update_returns=False,  # bot row missing during apply
    )
    with _TestClient(app_with_mocks, raise_server_exceptions=False) as raw_client:
        response = raw_client.post(
            "/api/configs/alpha/apply",
            json={
                "yaml_text": _VALID_YAML_ALPHA,
                "applied_by": "operator",
                "notes": None,
            },
        )
    assert response.status_code >= 500


def test_apply_audit_failure_propagates_5xx(
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit insert raises mid-tx → 5xx propagates (rollback contract)."""
    from fastapi.testclient import TestClient as _TestClient

    inserted = _make_bc_row(version=1, config_yaml=_VALID_YAML_ALPHA)
    _, _, _, _, audit = _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        inserted_row=inserted,
    )
    audit.side_effect = RuntimeError("audit failure simulated")
    with _TestClient(app_with_mocks, raise_server_exceptions=False) as raw_client:
        response = raw_client.post(
            "/api/configs/alpha/apply",
            json={
                "yaml_text": _VALID_YAML_ALPHA,
                "applied_by": "operator",
                "notes": None,
            },
        )
    assert response.status_code >= 500


def test_apply_actor_correlation_id_now_fn_threaded_to_audit(
    client: TestClient,
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#5 + WG#6 + WG#7 — actor=lan:testclient, correlation_id from header, now_fn=FIXED_NOW."""
    inserted = _make_bc_row(version=1, config_yaml=_VALID_YAML_ALPHA)
    _, _, _, _, audit = _patch_apply_helpers(
        monkeypatch,
        before_row=None,
        max_version=0,
        inserted_row=inserted,
    )
    app_with_mocks.state.now_fn = lambda: _FIXED_NOW
    client.post(
        "/api/configs/alpha/apply",
        json={"yaml_text": _VALID_YAML_ALPHA, "applied_by": "operator", "notes": None},
        headers={"X-Correlation-ID": "cid-apply-test"},
    )
    kwargs = _kwargs_of(audit)
    assert kwargs["actor"] == "lan:testclient"
    assert kwargs["correlation_id"] == "cid-apply-test"
    assert kwargs["occurred_at"] == _FIXED_NOW
    assert kwargs["action"] == "bot_config.apply"
    assert kwargs["entity_type"] == "bot_config"
    assert kwargs["entity_id"] == "alpha"
