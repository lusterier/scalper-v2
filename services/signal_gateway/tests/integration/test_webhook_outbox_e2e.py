"""T-537b — signal-gateway outbox integration test (testcontainer PG + mocked NATS).

Two complementary tests cover the full pipeline:

1. **test_webhook_writes_signals_row_and_outbox_row_in_same_tx** — POST /webhook
   → 200 + signal_id; ``signals`` row + ``outbox_events`` row both INSERTed in
   the same tx (atomic per OQ-1). Uses sync ``TestClient`` (Starlette runs in
   a sub-thread loop); DB state verified via direct asyncpg connection.

2. **test_outbox_relay_picks_up_pending_row_and_publishes_via_bus** — given
   an outbox_events row pre-seeded into the migrated DB, run
   ``OutboxRelayWorker._run_one_batch()`` against a real pool + mocked bus;
   verify ``bus.publish`` called once with constructed envelope + outbox row's
   ``published_at`` flipped (mark_published).

Split into two tests because a single TestClient + manual relay trigger hits
asyncio cross-loop issues (TestClient runs lifespan in a Starlette sub-thread
loop while pytest-asyncio uses its own loop). Each test exercises one half of
the pipeline; together they prove the integration without cross-loop awaits.

Skipped at collection when ``POSTGRES_TEST_DSN`` is unset.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi.testclient import TestClient

from packages.db import create_pool
from packages.outbox import OutboxRelaySettings, OutboxRelayWorker, insert_outbox_event
from services.signal_gateway.app.config import Settings
from services.signal_gateway.app.main import create_app

_TEST_HMAC_SECRET = "e2e-test-secret-padded-32chars!!"


def _hmac_sig(body: bytes, secret: str = _TEST_HMAC_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture
def outbox_e2e_app(
    migrated_db_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, MagicMock]:
    """Spin up signal-gateway app with real PG + mocked NATS bus."""
    monkeypatch.setenv("DATABASE_URL", migrated_db_dsn)
    monkeypatch.setenv("NATS_URL", "nats://unused-mocked:4222")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", _TEST_HMAC_SECRET)
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")

    mock_bus = MagicMock()
    mock_bus.connect = AsyncMock()
    mock_bus.close = AsyncMock()
    mock_bus.publish = AsyncMock()

    monkeypatch.setattr(
        "services.signal_gateway.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )

    settings = Settings()  # type: ignore[call-arg]
    app = create_app(settings=settings)
    return app, mock_bus


def test_webhook_writes_signals_row_and_outbox_row_in_same_tx(
    outbox_e2e_app: tuple[object, MagicMock],
) -> None:
    """T-537b OQ-1 — POST /webhook → atomic insert_signal + insert_outbox_event."""
    app, _mock_bus = outbox_e2e_app
    body = (
        b'{"symbol":"BTCUSDT.P","action":"LONG","source":"e2e-outbox",'
        b'"idempotency_key":"e2e-outbox-1","rsi":14.2}'
    )
    dsn = os.environ["DATABASE_URL"]

    with TestClient(app) as c:  # type: ignore[arg-type]
        response = c.post(
            "/webhook",
            content=body,
            headers={"X-Signature": _hmac_sig(body)},
        )
    assert response.status_code == 200
    signal_id = response.json()["signal_id"]

    # Direct asyncpg connect for verification (separate loop from TestClient
    # but DB state is already committed at this point).
    import asyncio

    async def _verify() -> None:
        conn = await asyncpg.connect(dsn=dsn)
        try:
            signal_row = await conn.fetchrow(
                "SELECT idempotency_key, ingestion_status FROM signals WHERE id = $1",
                signal_id,
            )
            assert signal_row is not None
            assert signal_row["idempotency_key"] == "e2e-outbox-1"
            assert signal_row["ingestion_status"] == "validated"

            outbox_row = await conn.fetchrow(
                "SELECT service, subject, correlation_id, payload, published_at, attempt_count "
                "FROM outbox_events WHERE correlation_id = $1",
                "e2e-outbox-1",
            )
            assert outbox_row is not None
            assert outbox_row["service"] == "signal-gateway"
            assert outbox_row["subject"] == "signals.validated"
            assert outbox_row["correlation_id"] == "e2e-outbox-1"
            assert outbox_row["published_at"] is None  # relay hasn't published yet
            assert outbox_row["attempt_count"] == 0
            outbox_payload = (
                json.loads(outbox_row["payload"])
                if isinstance(outbox_row["payload"], str)
                else outbox_row["payload"]
            )
            assert outbox_payload["idempotency_key"] == "e2e-outbox-1"
            assert outbox_payload["symbol"] == "BTCUSDT"
        finally:
            await conn.close()

    asyncio.run(_verify())


@pytest.mark.asyncio
async def test_outbox_relay_picks_up_pending_row_and_publishes_via_bus(
    migrated_db_dsn: str,
) -> None:
    """T-537b — relay-side coverage: pre-seeded outbox row → relay → bus.publish + mark_published.

    Companion to ``test_webhook_writes_signals_row_and_outbox_row_in_same_tx``.
    Together they cover the full insert→relay→publish pipeline; split because
    a single TestClient + manual relay trigger hits cross-loop issues.
    """
    pool = await create_pool(migrated_db_dsn, application_name="t537b-relay-test")
    try:
        # Seed an outbox_events row mirroring what webhook would write.
        seed_payload = {
            "source": "e2e-relay",
            "idempotency_key": "relay-test-1",
            "symbol": "BTCUSDT",
            "action": "LONG",
        }
        async with pool.acquire() as conn:
            event_id = await insert_outbox_event(
                conn,
                service="signal-gateway",
                subject="signals.validated",
                correlation_id="relay-test-1",
                payload=seed_payload,
                created_at=datetime(2026, 5, 9, 12, 0, 0, tzinfo=UTC),
            )

        # Run a single relay batch against the seeded row.
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        bound_logger = MagicMock()
        worker = OutboxRelayWorker(
            pool=pool,
            bus=mock_bus,
            service="signal-gateway",
            settings=OutboxRelaySettings(),
            bound_logger=bound_logger,
        )
        processed = await worker._run_one_batch()
        assert processed == 1

        # Verify bus.publish call args.
        mock_bus.publish.assert_awaited_once()
        call_args = mock_bus.publish.await_args
        assert call_args.args[0] == "signals.validated"
        envelope = call_args.args[1]
        assert envelope.correlation_id == "relay-test-1"
        assert envelope.publisher == "signal-gateway"
        assert envelope.payload == seed_payload

        # Verify mark_published flipped published_at.
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT published_at FROM outbox_events WHERE id = $1",
                event_id,
            )
            assert row is not None
            assert row["published_at"] is not None
    finally:
        await pool.close()
