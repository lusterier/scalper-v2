"""Tests for ``/api/backtests/*`` endpoints (T-407).

Mocks at the router import boundary
(``services.analytics_api.app.routers.backtests``) per WG#9 + T-401b
precedent. Pin: validate-before-tx ordering (WG#1), 2-helper same-conn
pin (WG#2), bot existence pre-check (CONCERN #4), config_hash raw bytes
(WG#7), now_fn single-call invariant (WG#8), audit emission shape
(WG#10 mirror), UUID serialization + 422 (CONCERN #3).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from packages.core.types import (
    BacktestStatus,
    BotStatus,
    ExchangeMode,
)
from packages.db.queries.analytics import BacktestRunRow, BotDetailRow

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient

_T_STARTED = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
_T_RANGE_START = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
_T_RANGE_END = datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)
_FIXED_NOW = datetime(2026, 5, 4, 13, 0, 0, tzinfo=UTC)
_RUN_UUID = UUID("12345678-1234-5678-1234-567812345678")


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


def _kwargs_of(mock: AsyncMock) -> dict[str, object]:
    assert mock.await_args is not None
    return dict(mock.await_args.kwargs)


def await_aenter_value(mock_pool: Any) -> Any:
    return mock_pool.acquire.return_value.__aenter__.return_value


def _make_bot_row(bot_id: str = "alpha") -> BotDetailRow:
    return BotDetailRow(
        bot_id=bot_id,
        display_name=f"{bot_id.title()} Bot",
        created_at=_T_RANGE_START,
        status=BotStatus.ACTIVE,
        exchange_mode=ExchangeMode.PAPER,
        config_hash="deadbeef" * 8,
        config_applied_at=_T_RANGE_START,
        meta={},
    )


def _make_run_row(
    *,
    run_id: UUID = _RUN_UUID,
    name: str = "alpha apr backtest",
    bot_id: str = "alpha",
    status: BacktestStatus = BacktestStatus.QUEUED,
    config_yaml: str = _VALID_YAML_ALPHA,
) -> BacktestRunRow:
    return BacktestRunRow(
        id=run_id,
        name=name,
        bot_id=bot_id,
        config_yaml=config_yaml,
        config_hash=hashlib.sha256(config_yaml.encode("utf-8")).hexdigest(),
        date_range_start=_T_RANGE_START,
        date_range_end=_T_RANGE_END,
        status=status,
        started_at=_T_STARTED,
        finished_at=None,
        summary=None,
        notes=None,
    )


def _patch_post_helpers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    bot_row: BotDetailRow | None,
    inserted_row: BacktestRunRow | None = None,
    insert_side_effect: BaseException | None = None,
    audit_side_effect: BaseException | None = None,
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Mock 3 POST helpers: select_bot_by_id + insert_backtest_run + insert_audit_event."""
    select_bot = AsyncMock(return_value=bot_row)
    if insert_side_effect is not None:
        insert = AsyncMock(side_effect=insert_side_effect)
    else:
        insert = AsyncMock(return_value=inserted_row)
    if audit_side_effect is not None:
        audit = AsyncMock(side_effect=audit_side_effect)
    else:
        audit = AsyncMock(return_value=42)
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.select_bot_by_id",
        select_bot,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.insert_backtest_run",
        insert,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.insert_audit_event",
        audit,
    )
    return select_bot, insert, audit


def _post_body(
    *,
    yaml_text: str = _VALID_YAML_ALPHA,
    bot_id: str = "alpha",
    name: str = "alpha apr backtest",
) -> dict[str, Any]:
    return {
        "name": name,
        "bot_id": bot_id,
        "config_yaml": yaml_text,
        "date_range_start": _T_RANGE_START.isoformat(),
        "date_range_end": _T_RANGE_END.isoformat(),
        "notes": None,
    }


# ---------------------------------------------------------------------------
# LIST endpoint
# ---------------------------------------------------------------------------


def test_list_backtests_default_returns_envelope(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.select_backtest_runs_paginated",
        AsyncMock(return_value=[_make_run_row()]),
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.count_backtest_runs",
        AsyncMock(return_value=1),
    )
    response = client.get("/api/backtests/")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["runs"]) == 1
    assert body["runs"][0]["status"] == "queued"


def test_list_backtests_filter_by_status_passes_enum_through(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.select_backtest_runs_paginated",
        select_mock,
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.count_backtest_runs",
        AsyncMock(return_value=0),
    )
    response = client.get("/api/backtests/?status=running")
    assert response.status_code == 200
    assert _kwargs_of(select_mock)["status"] == BacktestStatus.RUNNING


def test_list_backtests_invalid_status_returns_422(
    client: TestClient,
) -> None:
    response = client.get("/api/backtests/?status=garbage")
    assert response.status_code == 422


def test_list_backtests_limit_and_offset_bounds_422(
    client: TestClient,
) -> None:
    assert client.get("/api/backtests/?limit=0").status_code == 422
    assert client.get("/api/backtests/?limit=999").status_code == 422
    assert client.get("/api/backtests/?offset=-1").status_code == 422


def test_list_backtests_inverted_range_returns_empty(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#9 — `from > to` not pre-validated; produces empty list (mirror T-402 pattern)."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.select_backtest_runs_paginated",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.count_backtest_runs",
        AsyncMock(return_value=0),
    )
    response = client.get(
        "/api/backtests/",
        params={
            "from": _T_RANGE_END.isoformat(),
            "to": _T_RANGE_START.isoformat(),
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["runs"] == []


# ---------------------------------------------------------------------------
# POST trigger endpoint
# ---------------------------------------------------------------------------


def test_post_backtest_creates_run_returns_202(
    client: TestClient,
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: 202 Accepted; status='queued'; started_at = app.state.now_fn()."""
    inserted = _make_run_row()
    _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
        inserted_row=inserted,
    )
    app_with_mocks.state.now_fn = lambda: _FIXED_NOW
    response = client.post("/api/backtests/", json=_post_body())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["bot_id"] == "alpha"


def test_post_backtest_invalid_yaml_422_no_db_write(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#1 — load_bot_config_from_string ValueError → 422 BEFORE insert; helpers NOT awaited."""
    select_bot, insert, audit = _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
    )
    response = client.post(
        "/api/backtests/",
        json=_post_body(yaml_text="not: valid: yaml: ::"),
    )
    assert response.status_code == 422
    select_bot.assert_not_awaited()
    insert.assert_not_awaited()
    audit.assert_not_awaited()


def test_post_backtest_yaml_missing_bot_id_returns_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drift CONCERN #1 — YAML parsing KeyError ('bot_id' missing) → 422 (NOT 500)."""
    select_bot, insert, audit = _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
    )
    yaml_no_bot_id = _VALID_YAML_ALPHA.replace("bot_id: alpha\n", "")
    response = client.post(
        "/api/backtests/",
        json=_post_body(yaml_text=yaml_no_bot_id),
    )
    assert response.status_code == 422
    select_bot.assert_not_awaited()
    insert.assert_not_awaited()
    audit.assert_not_awaited()


def test_post_backtest_yaml_bot_id_mismatch_422(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#1 step 3 — body bot_id != parsed YAML bot_id → 422 BEFORE insert."""
    select_bot, insert, audit = _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
    )
    response = client.post(
        "/api/backtests/",
        json=_post_body(bot_id="beta"),  # YAML says "alpha"
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "alpha" in detail
    assert "beta" in detail
    select_bot.assert_not_awaited()
    insert.assert_not_awaited()
    audit.assert_not_awaited()


def test_post_backtest_unknown_bot_id_returns_404(
    client: TestClient,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONCERN #4 — select_bot_by_id None → 404; insert + audit NOT awaited."""
    select_bot, insert, audit = _patch_post_helpers(
        monkeypatch,
        bot_row=None,  # bot_id not in registry
    )
    response = client.post("/api/backtests/", json=_post_body())
    assert response.status_code == 404
    assert "alpha" in response.json()["detail"]
    select_bot.assert_awaited_once()
    insert.assert_not_awaited()
    audit.assert_not_awaited()
    # Tx should NEVER have opened (404 before transaction context).
    fake_conn = await_aenter_value(mock_pool)
    fake_conn.transaction.assert_not_called()


def test_post_backtest_oversize_yaml_422(
    client: TestClient,
) -> None:
    """Pydantic Field max_length=200_000 enforced."""
    huge_yaml = "x" * 200_001
    response = client.post(
        "/api/backtests/",
        json=_post_body(yaml_text=huge_yaml),
    )
    assert response.status_code == 422


def test_post_backtest_inverted_date_range_422(
    client: TestClient,
) -> None:
    """Pydantic model_validator (mode='after') enforces date_range_start < date_range_end."""
    body = _post_body()
    body["date_range_start"] = _T_RANGE_END.isoformat()
    body["date_range_end"] = _T_RANGE_START.isoformat()
    response = client.post("/api/backtests/", json=body)
    assert response.status_code == 422


def test_post_backtest_emits_audit_row_in_same_tx(
    client: TestClient,
    mock_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#2 — both insert_backtest_run AND insert_audit_event awaited on SAME conn handle."""
    inserted = _make_run_row()
    _, insert, audit = _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
        inserted_row=inserted,
    )
    client.post("/api/backtests/", json=_post_body())
    expected_conn = await_aenter_value(mock_pool)
    assert insert.await_args is not None
    assert audit.await_args is not None
    assert insert.await_args.args[0] is expected_conn
    assert audit.await_args.args[0] is expected_conn


def test_post_backtest_audit_after_state_excludes_config_yaml(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#10 mirror — after_state has 11-key shape; config_yaml excluded for size discipline."""
    inserted = _make_run_row()
    _, _, audit = _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
        inserted_row=inserted,
    )
    client.post("/api/backtests/", json=_post_body())
    kwargs = _kwargs_of(audit)
    after = kwargs["after_state"]
    assert isinstance(after, dict)
    assert "config_yaml" not in after
    expected_keys = {
        "id",
        "name",
        "bot_id",
        "config_hash",
        "date_range_start",
        "date_range_end",
        "status",
        "started_at",
        "finished_at",
        "summary",
        "notes",
    }
    assert set(after.keys()) == expected_keys
    # before_state is None (creation).
    assert kwargs["before_state"] is None


def test_post_backtest_config_hash_includes_trailing_whitespace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#7 — raw bytes policy: trailing whitespace CHANGES hash (no .strip())."""
    inserted = _make_run_row()
    _, insert, _ = _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
        inserted_row=inserted,
    )
    yaml_with_extra = _VALID_YAML_ALPHA + "\n\n"
    client.post(
        "/api/backtests/",
        json=_post_body(yaml_text=yaml_with_extra),
    )
    expected = hashlib.sha256(yaml_with_extra.encode("utf-8")).hexdigest()
    not_stripped = hashlib.sha256(_VALID_YAML_ALPHA.encode("utf-8")).hexdigest()
    assert expected != not_stripped
    assert _kwargs_of(insert)["config_hash"] == expected


def test_post_backtest_started_at_equals_audit_occurred_at(
    client: TestClient,
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8 — single now_fn() call: backtest_runs.started_at == audit.occurred_at."""
    inserted = _make_run_row()
    _, insert, audit = _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
        inserted_row=inserted,
    )
    app_with_mocks.state.now_fn = lambda: _FIXED_NOW
    client.post("/api/backtests/", json=_post_body())
    insert_kwargs = _kwargs_of(insert)
    audit_kwargs = _kwargs_of(audit)
    assert insert_kwargs["started_at"] == _FIXED_NOW
    assert audit_kwargs["occurred_at"] == _FIXED_NOW
    assert insert_kwargs["started_at"] == audit_kwargs["occurred_at"]


def test_post_backtest_actor_correlation_id_threaded_to_audit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#1 step 5 — actor=lan:testclient + X-Correlation-ID header → audit kwargs."""
    inserted = _make_run_row()
    _, _, audit = _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
        inserted_row=inserted,
    )
    client.post(
        "/api/backtests/",
        json=_post_body(),
        headers={"X-Correlation-ID": "cid-bt-test"},
    )
    kwargs = _kwargs_of(audit)
    assert kwargs["actor"] == "lan:testclient"
    assert kwargs["correlation_id"] == "cid-bt-test"
    assert kwargs["action"] == "backtest_run.queued"
    assert kwargs["entity_type"] == "backtest_run"
    assert kwargs["entity_id"] == str(_RUN_UUID)


def test_post_backtest_logs_after_commit(
    client: TestClient,
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#9 / T-405 WG#9 — logger.info emitted AFTER tx commits (post-async-with)."""
    inserted = _make_run_row()
    _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
        inserted_row=inserted,
    )
    log_calls: list[tuple[str, dict[str, Any]]] = []
    app_with_mocks.state.logger.info = lambda event, **kw: log_calls.append((event, kw))
    response = client.post("/api/backtests/", json=_post_body())
    assert response.status_code == 202
    assert any(call[0] == "backtest_run.queued" for call in log_calls)
    matching = next(call for call in log_calls if call[0] == "backtest_run.queued")
    assert matching[1]["run_id"] == str(_RUN_UUID)
    assert matching[1]["bot_id"] == "alpha"


def test_post_backtest_log_NOT_emitted_when_audit_fails(
    app_with_mocks: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#9 — audit insert raises mid-tx → 5xx; logger.info NOT called."""
    from fastapi.testclient import TestClient as _TestClient

    inserted = _make_run_row()
    _patch_post_helpers(
        monkeypatch,
        bot_row=_make_bot_row(),
        inserted_row=inserted,
        audit_side_effect=RuntimeError("audit failure simulated"),
    )
    log_calls: list[tuple[str, dict[str, Any]]] = []
    app_with_mocks.state.logger.info = lambda event, **kw: log_calls.append((event, kw))
    with _TestClient(app_with_mocks, raise_server_exceptions=False) as raw_client:
        response = raw_client.post("/api/backtests/", json=_post_body())
    assert response.status_code >= 500
    assert all(call[0] != "backtest_run.queued" for call in log_calls)


# ---------------------------------------------------------------------------
# GET detail endpoint
# ---------------------------------------------------------------------------


def test_get_backtest_by_id_returns_uuid_as_string(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONCERN #3 — Pydantic v2 UUID → JSON string; UUID4-ish hex regex match."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.select_backtest_run_by_id",
        AsyncMock(return_value=_make_run_row()),
    )
    response = client.get(f"/api/backtests/{_RUN_UUID}")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["id"], str)
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", body["id"])


def test_get_backtest_by_id_invalid_uuid_path_param_422(
    client: TestClient,
) -> None:
    """CONCERN #3 — FastAPI Path UUID coercion auto-422 on garbage."""
    response = client.get("/api/backtests/not-a-uuid")
    assert response.status_code == 422


def test_get_backtest_by_id_miss_returns_404(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown UUID → 404 with detail format."""
    monkeypatch.setattr(
        "services.analytics_api.app.routers.backtests.select_backtest_run_by_id",
        AsyncMock(return_value=None),
    )
    missing = uuid4()
    response = client.get(f"/api/backtests/{missing}")
    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]
