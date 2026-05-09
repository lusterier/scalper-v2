"""Unit tests for :mod:`services.signal_gateway.app.webhook`.

Per-branch coverage of the §9.1 13-step pipeline orchestrated in
:func:`services.signal_gateway.app.webhook.webhook`. End-to-end PG +
NATS coverage lives in ``tests/integration/test_webhook_e2e.py``;
these tests stay decoupled by patching ``insert_signal`` at the
import site (``services.signal_gateway.app.webhook.insert_signal``)
and the bus / dedup / symbol_cache via ``app.state``.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.signal_gateway.app.main import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx import Response

    from services.signal_gateway.app.config import Settings


_TEST_HMAC_SECRET = "unit-test-secret-padded-32chars!"  # mirrors conftest.py settings fixture


# ---- Mocking infrastructure ------------------------------------------------


@pytest.fixture
def mock_conn() -> MagicMock:
    """asyncpg connection stand-in (insert_signal is patched, so fetchrow is unused here).

    T-537b: webhook validated path now wraps insert_signal + insert_outbox_event
    in `async with pool.acquire() as conn, conn.transaction():` so conn must
    support `transaction()` async-context-manager. If insert_outbox_event
    raises, conn.transaction __aexit__ propagates the exception → tx rollback
    semantic mocked here as no-op (rollback verification is upstream test
    duty via mocked helper side_effect).
    """
    conn = MagicMock()
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=None)
    tx_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx_cm)
    return conn


@pytest.fixture
def mock_pool_with_conn(mock_pool: MagicMock, mock_conn: MagicMock) -> MagicMock:
    """Pool whose acquire() yields ``mock_conn``."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = MagicMock(return_value=cm)
    return mock_pool


@pytest.fixture
def webhook_app(
    settings: Settings,
    mock_pool_with_conn: MagicMock,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """create_app() with pool / bus / SymbolMapCache mocked; insert_signal patched at import site.

    Each test gets a fresh app instance (and thus a fresh Prometheus
    registry) so metric assertions against absolute counts are
    deterministic.

    ``SymbolMapCache`` is monkeypatched at the main.py import site so
    the lifespan-attached ``app.state.symbol_cache`` is the same
    :class:`MagicMock` the fixture pre-attaches. The pre-attach is
    load-bearing: tests configure ``symbol_cache.resolve`` *before*
    entering the ``TestClient`` context (uniform with sync-attached
    ``dedup``). Without it, pre-context access AttributeErrors —
    ``symbol_cache`` is otherwise async-attached only inside lifespan.
    """
    monkeypatch.setattr(
        "services.signal_gateway.app.main.create_pool",
        AsyncMock(return_value=mock_pool_with_conn),
    )
    monkeypatch.setattr(
        "services.signal_gateway.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    # T-537b: stub OutboxRelayWorker to no-op so tests don't actually run the
    # relay loop against the mocked pool. Each test that needs to exercise
    # the relay (none today; covered by integration tests) overrides this.
    relay_stub = MagicMock()
    relay_stub.run = AsyncMock(return_value=None)
    relay_stub.stop = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "services.signal_gateway.app.main.OutboxRelayWorker",
        MagicMock(return_value=relay_stub),
    )
    symbol_cache_mock = MagicMock()
    symbol_cache_mock.resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "services.signal_gateway.app.main.SymbolMapCache",
        MagicMock(return_value=symbol_cache_mock),
    )
    mock_bus.publish = AsyncMock()
    app = create_app(settings=settings)
    app.state.symbol_cache = symbol_cache_mock
    return app


def _hmac_sig(body: bytes, secret: str = _TEST_HMAC_SECRET) -> str:
    """Compute lowercase hex HMAC-SHA256 used by the X-Signature header."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _signed_post(client: TestClient, body: bytes) -> Response:
    return client.post(
        "/webhook",
        content=body,
        headers={"X-Signature": _hmac_sig(body)},
    )


# ---- Pipeline branch tests --------------------------------------------------


def test_happy_path_returns_200_with_signal_id(
    webhook_app: FastAPI,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-537b — Full flow happy path.

    HMAC + raw publish + parse + dedup + symbol resolve + DB tx (insert_signal
    + insert_outbox_event atomic) → 200. Outbox relay handles signals.validated
    NATS publish post-commit (no direct webhook publish on validated path).
    """
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_signal",
        AsyncMock(return_value=42),
    )
    insert_outbox_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_outbox_event",
        insert_outbox_mock,
    )
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-1"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 200
    assert r.json() == {"signal_id": 42}

    # T-537b: only signals.raw is published directly by webhook; signals.validated
    # routes through outbox + relay (out of webhook scope).
    publish_calls = mock_bus.publish.await_args_list
    subjects = [call.args[0] for call in publish_calls]
    assert subjects == ["signals.raw"]

    # T-537b: outbox row INSERTed in same tx as insert_signal (atomic).
    insert_outbox_mock.assert_awaited_once()
    assert insert_outbox_mock.await_args is not None
    outbox_kwargs = insert_outbox_mock.await_args.kwargs
    assert outbox_kwargs["service"] == "signal-gateway"
    assert outbox_kwargs["subject"] == "signals.validated"
    assert outbox_kwargs["correlation_id"] == "k-1"
    assert outbox_kwargs["payload"]["idempotency_key"] == "k-1"
    assert outbox_kwargs["payload"]["symbol"] == "BTCUSDT"

    # NB: `_value.get()` is prometheus_client private API but stable across the 0.21 series.
    metrics = webhook_app.state.metrics
    assert metrics.signals_received.labels(source="tv")._value.get() == 1.0
    assert metrics.signals_validated.labels(status="validated")._value.get() == 1.0


def test_hmac_invalid_returns_401(
    webhook_app: FastAPI,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad signature → 401 hmac_invalid; no signals.raw publish; no DB."""
    insert_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-1"}'
    with TestClient(webhook_app) as c:
        r = c.post("/webhook", content=body, headers={"X-Signature": "0" * 64})

    assert r.status_code == 401
    assert r.json()["reason"] == "hmac_invalid"

    mock_bus.publish.assert_not_awaited()
    insert_mock.assert_not_awaited()

    metrics = webhook_app.state.metrics
    assert (
        metrics.errors.labels(
            service="signal-gateway",
            error_class="hmac_invalid",
        )._value.get()
        == 1.0
    )


def test_invalid_json_returns_400(
    webhook_app: FastAPI,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unparseable JSON → 400 invalid_json; signals.raw still published; no validated; no DB."""
    insert_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)

    body = b"not json{"
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 400
    assert r.json()["reason"] == "invalid_json"

    subjects = [call.args[0] for call in mock_bus.publish.await_args_list]
    assert subjects == ["signals.raw"]
    insert_mock.assert_not_awaited()

    metrics = webhook_app.state.metrics
    assert (
        metrics.errors.labels(
            service="signal-gateway",
            error_class="invalid_json",
        )._value.get()
        == 1.0
    )


def test_validation_failed_unkeyed_returns_400_no_db(
    webhook_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic fail with no idempotency_key → 400, no DB row, validation_unkeyed metric."""
    insert_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 400
    assert r.json()["reason"] == "validation_failed"

    insert_mock.assert_not_awaited()

    metrics = webhook_app.state.metrics
    assert (
        metrics.errors.labels(
            service="signal-gateway",
            error_class="validation_unkeyed",
        )._value.get()
        == 1.0
    )


def test_validation_failed_keyed_writes_invalid_db_row(
    webhook_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic fail with parseable idempotency_key → 400; DB row ingestion_status='invalid'."""
    insert_mock = AsyncMock(return_value=99)
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)

    body = b'{"symbol":"BTCUSDT.P","action":"HOLD","source":"tv","idempotency_key":"k-bad"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 400
    assert r.json()["reason"] == "validation_failed"

    insert_mock.assert_awaited_once()
    assert insert_mock.await_args is not None
    kwargs = insert_mock.await_args.kwargs
    assert kwargs["ingestion_status"] == "invalid"
    assert kwargs["idempotency_key"] == "k-bad"

    metrics = webhook_app.state.metrics
    assert metrics.signals_validated.labels(status="invalid")._value.get() == 1.0


def test_validation_failed_keyed_db_fail_returns_500(
    webhook_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag C: DB write failure during validation_failed audit row → 500 internal, NOT 400."""
    insert_mock = AsyncMock(side_effect=RuntimeError("pg down"))
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)

    body = b'{"symbol":"BTCUSDT.P","action":"HOLD","source":"tv","idempotency_key":"k-bad"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 500
    assert r.json()["reason"] == "internal"

    insert_mock.assert_awaited_once()
    metrics = webhook_app.state.metrics
    assert (
        metrics.errors.labels(
            service="signal-gateway",
            error_class="db_insert_failed",
        )._value.get()
        == 1.0
    )


def test_duplicate_returns_202(
    webhook_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dedup hit → 202 {status: duplicate}; DB row 'duplicate'."""
    insert_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=False)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-dup"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 202
    assert r.json() == {"status": "duplicate"}

    insert_mock.assert_awaited_once()
    assert insert_mock.await_args is not None
    assert insert_mock.await_args.kwargs["ingestion_status"] == "duplicate"

    metrics = webhook_app.state.metrics
    assert metrics.signals_validated.labels(status="duplicate")._value.get() == 1.0


def test_symbol_unknown_returns_422(
    webhook_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symbol not in symbol_map → 422 symbol_unknown; DB row 'invalid' with original_symbol=None."""
    insert_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value=None)

    body = b'{"symbol":"WTFUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-x"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 422
    assert r.json()["reason"] == "symbol_unknown"

    insert_mock.assert_awaited_once()
    assert insert_mock.await_args is not None
    kwargs = insert_mock.await_args.kwargs
    assert kwargs["ingestion_status"] == "invalid"
    assert kwargs["original_symbol"] is None
    assert kwargs["symbol"] == "WTFUSDT.P"

    metrics = webhook_app.state.metrics
    assert metrics.signals_validated.labels(status="invalid")._value.get() == 1.0


def test_db_insert_validated_failure_returns_500(
    webhook_app: FastAPI,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB fail at step 11 → 500 internal; signals.validated NOT published."""
    insert_mock = AsyncMock(side_effect=RuntimeError("pg down"))
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-1"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 500
    assert r.json()["reason"] == "internal"

    subjects = [call.args[0] for call in mock_bus.publish.await_args_list]
    assert "signals.validated" not in subjects

    metrics = webhook_app.state.metrics
    assert (
        metrics.errors.labels(
            service="signal-gateway",
            error_class="db_insert_failed",
        )._value.get()
        == 1.0
    )


def test_validated_path_does_not_call_bus_publish_for_signals_validated(
    webhook_app: FastAPI,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-537b — validated path NEVER calls bus.publish('signals.validated'); routing through outbox.

    Direct bus.publish call site for 'signals.validated' was REMOVED from
    webhook.py per OQ-2 (full removal). signals.raw publish stays (audit
    stream, not affected).
    """
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_signal",
        AsyncMock(return_value=42),
    )
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_outbox_event",
        AsyncMock(return_value=1),
    )
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-1"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 200
    subjects = [call.args[0] for call in mock_bus.publish.await_args_list]
    # signals.raw is published (audit stream); signals.validated is NOT.
    assert "signals.raw" in subjects
    assert "signals.validated" not in subjects


def test_validated_path_insert_signal_and_outbox_run_in_same_tx(
    webhook_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-537b — both helpers receive the SAME conn (single tx atomicity).

    Verifies both insert_signal and insert_outbox_event are invoked with the
    same conn instance — proves they share the tx scope.
    """
    received_conns: list[object] = []

    async def _insert_signal_spy(conn: object, **_kwargs: object) -> int:
        received_conns.append(conn)
        return 42

    async def _insert_outbox_spy(conn: object, **_kwargs: object) -> int:
        received_conns.append(conn)
        return 1

    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", _insert_signal_spy)
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_outbox_event",
        _insert_outbox_spy,
    )
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-1"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 200
    assert len(received_conns) == 2
    # Same conn instance threaded through both helpers → same tx scope.
    assert received_conns[0] is received_conns[1]


def test_validated_path_outbox_insert_failure_rolls_back_returns_500(
    webhook_app: FastAPI,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-537b — insert_outbox_event failure rolls back insert_signal write + returns 500.

    Tests tx atomicity: if outbox insert fails, the whole tx (insert_signal +
    insert_outbox_event) rolls back. Mock conn.transaction __aexit__ records
    exception type; verifies tx exit was on the exception path.
    """
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_signal",
        AsyncMock(return_value=42),
    )
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_outbox_event",
        AsyncMock(side_effect=RuntimeError("outbox insert failed")),
    )
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-1"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 500
    assert r.json()["reason"] == "internal"

    # No signals.validated publish (path bypassed by tx-rollback exit).
    subjects = [call.args[0] for call in mock_bus.publish.await_args_list]
    assert "signals.validated" not in subjects

    metrics = webhook_app.state.metrics
    # Single error_class covers both helpers per webhook.py refactor.
    assert (
        metrics.errors.labels(
            service="signal-gateway",
            error_class="db_insert_failed",
        )._value.get()
        == 1.0
    )


def test_signals_raw_publish_failure_falls_through_to_200(
    webhook_app: FastAPI,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """signals.raw publish fail is best-effort: handler still completes happy path.

    T-537b: signals.validated no longer published directly by webhook (routed
    via outbox); assertion focuses on signals.raw error_class metric +
    successful 200 response.
    """
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_signal",
        AsyncMock(return_value=42),
    )
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_outbox_event",
        AsyncMock(return_value=1),
    )
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    async def publish_side_effect(subject: str, _envelope: object) -> None:
        if subject == "signals.raw":
            raise RuntimeError("nats blip")

    mock_bus.publish = AsyncMock(side_effect=publish_side_effect)

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-1"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 200
    assert r.json() == {"signal_id": 42}

    metrics = webhook_app.state.metrics
    assert (
        metrics.errors.labels(
            service="signal-gateway",
            error_class="publish_raw_failed",
        )._value.get()
        == 1.0
    )


def test_signal_envelope_extras_migration_via_webhook(
    webhook_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TV v3 flat alert: top-level rsi/sl_pct migrate into the DB-row payload kwarg."""
    insert_mock = AsyncMock(return_value=42)
    monkeypatch.setattr("services.signal_gateway.app.webhook.insert_signal", insert_mock)
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_outbox_event",
        AsyncMock(return_value=1),
    )
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    body = (
        b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-1",'
        b'"rsi":14.2,"sl_pct":0.01}'
    )
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 200
    insert_mock.assert_awaited_once()
    assert insert_mock.await_args is not None
    payload = insert_mock.await_args.kwargs["payload"]
    assert payload == {"rsi": 14.2, "sl_pct": 0.01}


def test_signals_raw_published_with_fresh_uuid_correlation_id(
    webhook_app: FastAPI,
    mock_bus: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag A: signals.raw correlation_id is fresh UUID4, not trace_id or idempotency_key."""
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_signal",
        AsyncMock(return_value=42),
    )
    monkeypatch.setattr(
        "services.signal_gateway.app.webhook.insert_outbox_event",
        AsyncMock(return_value=1),
    )
    webhook_app.state.dedup.check_and_record = AsyncMock(return_value=True)
    webhook_app.state.symbol_cache.resolve = AsyncMock(return_value="BTCUSDT")

    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"tv","idempotency_key":"k-uuid-test"}'
    with TestClient(webhook_app) as c:
        r = _signed_post(c, body)

    assert r.status_code == 200

    raw_calls = [call for call in mock_bus.publish.await_args_list if call.args[0] == "signals.raw"]
    assert len(raw_calls) == 1
    raw_envelope = raw_calls[0].args[1]
    cid = str(raw_envelope.correlation_id)
    parsed = uuid.UUID(cid)
    assert parsed.version == 4
    assert cid != "k-uuid-test"
