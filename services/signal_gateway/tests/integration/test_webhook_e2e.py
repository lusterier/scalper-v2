"""End-to-end integration tests for the signal-gateway ``/webhook`` pipeline.

Two scenarios — happy path and duplicate edge — exercising the full
§9.1 pipeline against real PostgreSQL (throwaway DB per test) and a
real NATS JetStream server. Per-test the conftest:

* migrates a fresh DB,
* connects a ``signals.validated`` subscriber BEFORE the
  signal-gateway lifespan starts,
* yields an :class:`E2EFixture` so the test can drive the app via
  :class:`fastapi.testclient.TestClient` and assert against both
  PG row content and the NATS-received envelope payload.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import asyncpg
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from packages.bus import MessageEnvelope

    from .conftest import E2EFixture


_TEST_HMAC_SECRET = "e2e-test-secret-padded-32chars!!"
# T-537b: post-relay delivery latency budget.
# Pre-T-537b: webhook published 'signals.validated' directly → expected ~50ms.
# Post-T-537b: webhook commits outbox row in tx → relay polls every
# poll_interval_s (default 1.0s) → publish + mark ≈ 1.5s typical.
# 2x typical cushion = 3s; 10s defensive against CI runner slowness.
# DO NOT trim below 10.0 without verifying CI test runtime headroom.
_NATS_DELIVERY_TIMEOUT_SECONDS = 10.0


def _hmac_sig(body: bytes, secret: str = _TEST_HMAC_SECRET) -> str:
    """Compute lowercase hex HMAC-SHA256 used by the X-Signature header."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


async def _wait_for_messages(received: list[MessageEnvelope], count: int) -> None:
    """Poll ``received`` until it has at least ``count`` envelopes or timeout."""
    deadline = time.monotonic() + _NATS_DELIVERY_TIMEOUT_SECONDS
    # ASYNC110 suggests asyncio.Event, but the subscriber handler is a
    # plain callback that just appends; threading an Event through it
    # couples test setup to a count-tracking event-fire pattern. The
    # bounded poll-with-timeout is the correct idiom here.
    while len(received) < count and time.monotonic() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.05)
    if len(received) < count:
        msg = f"NATS subscriber received {len(received)} of expected {count} within timeout"
        raise AssertionError(msg)


async def test_webhook_full_round_trip(webhook_e2e: E2EFixture) -> None:
    """Happy path: 200 + signal_id + DB row 'validated' + NATS envelope shape.

    T-537b: NATS envelope arrives via outbox + relay (poll → bus.publish →
    mark_published), NOT via direct webhook publish. Latency budget extended
    to 10s per `_NATS_DELIVERY_TIMEOUT_SECONDS` comment above.
    """
    body = (
        b'{"symbol":"BTCUSDT.P","action":"LONG","source":"e2e",'
        b'"idempotency_key":"e2e-happy-1","rsi":14.2}'
    )
    # T-537b: TestClient context KEPT OPEN through _wait_for_messages so the
    # outbox relay (lifespan-hosted asyncio task) stays alive long enough to
    # poll + publish. Pre-T-537b webhook published directly inside the request
    # so the wait could happen post-context. Now publish is async via relay.
    with TestClient(webhook_e2e.app) as c:
        response = c.post(
            "/webhook",
            content=body,
            headers={"X-Signature": _hmac_sig(body)},
        )

        assert response.status_code == 200
        payload = response.json()
        assert "signal_id" in payload
        signal_id = payload["signal_id"]
        assert isinstance(signal_id, int)

        dsn = os.environ["DATABASE_URL"]
        conn = await asyncpg.connect(dsn=dsn)
        try:
            row = await conn.fetchrow(
                "SELECT id, symbol, original_symbol, action, source, "
                "idempotency_key, ingestion_status, payload, correlation_id "
                "FROM signals WHERE id = $1",
                signal_id,
            )
        finally:
            await conn.close()
        assert row is not None
        assert row["symbol"] == "BTCUSDT"
        assert row["original_symbol"] == "BTCUSDT.P"
        assert row["action"] == "LONG"
        assert row["source"] == "e2e"
        assert row["idempotency_key"] == "e2e-happy-1"
        assert row["ingestion_status"] == "validated"
        assert row["correlation_id"] == "e2e-happy-1"
        db_payload = (
            json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        )
        assert db_payload == {"rsi": 14.2}

        await _wait_for_messages(webhook_e2e.received, 1)
    envelope = webhook_e2e.received[0]
    assert envelope.publisher == "signal-gateway"
    assert envelope.correlation_id == "e2e-happy-1"
    assert envelope.payload["symbol"] == "BTCUSDT"
    assert envelope.payload["original_symbol"] == "BTCUSDT.P"
    assert envelope.payload["action"] == "LONG"
    assert envelope.payload["source"] == "e2e"
    assert envelope.payload["idempotency_key"] == "e2e-happy-1"
    assert envelope.payload["payload"] == {"rsi": 14.2}

    received_at = datetime.fromisoformat(envelope.payload["received_at"])
    expires_at = datetime.fromisoformat(envelope.payload["expires_at"])
    assert expires_at - received_at == timedelta(seconds=120)


async def test_webhook_duplicate_round_trip(webhook_e2e: E2EFixture) -> None:  # noqa: D401, RUF100
    # T-537b: post-relay delivery semantics — see test_webhook_full_round_trip
    # docstring for the latency budget rationale.
    """Same idempotency_key twice -> first 200 'validated', second 202 'duplicate', 2 PG rows."""
    body = b'{"symbol":"BTCUSDT.P","action":"LONG","source":"e2e","idempotency_key":"e2e-dup-1"}'
    sig = _hmac_sig(body)
    # T-537b: TestClient context KEPT OPEN through _wait_for_messages — see
    # test_webhook_full_round_trip for rationale.
    with TestClient(webhook_e2e.app) as c:
        first = c.post("/webhook", content=body, headers={"X-Signature": sig})
        second = c.post("/webhook", content=body, headers={"X-Signature": sig})

        assert first.status_code == 200
        assert second.status_code == 202
        assert second.json() == {"status": "duplicate"}

        dsn = os.environ["DATABASE_URL"]
        conn = await asyncpg.connect(dsn=dsn)
        try:
            rows = await conn.fetch(
                "SELECT ingestion_status FROM signals "
                "WHERE idempotency_key = $1 ORDER BY received_at",
                "e2e-dup-1",
            )
        finally:
            await conn.close()
        statuses = [r["ingestion_status"] for r in rows]
        assert statuses == ["validated", "duplicate"]

        await _wait_for_messages(webhook_e2e.received, 1)
    assert len(webhook_e2e.received) == 1
    assert webhook_e2e.received[0].correlation_id == "e2e-dup-1"
