"""Fixtures for signal-gateway end-to-end integration tests (T-015b2b).

Spins up a throwaway PostgreSQL database per test (migrated via
``alembic upgrade head``, mirroring ``tests/integration/migrations/conftest.py``),
attaches a NATS JetStream subscription on ``signals.validated`` BEFORE
the signal-gateway lifespan starts so test-published validated
envelopes are observable, and yields an :class:`E2EFixture` bundling
the FastAPI app + the running subscriber's received-message list.

DSN comes from ``POSTGRES_TEST_DSN`` and the NATS server URL from
``NATS_TEST_URL``. If either is unset, every test in this directory
is skipped at module collection time — keeping CI-fast green until
T-016 lights testcontainers.

The subscriber NatsClient uses a per-test unique connection name
(``test-subscriber-<uuid8>``) to avoid orphan-consumer accumulation
across the test run; ``close()`` runs in ``finally`` so dirty
teardown still drains the subscription.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from packages.bus import MessageEnvelope, NatsClient
from packages.observability import get_logger
from services.signal_gateway.app.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


_DSN_ENV_VAR = "POSTGRES_TEST_DSN"
_NATS_ENV_VAR = "NATS_TEST_URL"
_ALEMBIC_URL_ENV_VAR = "POSTGRES_URL"
# parents[4] (vs. parents[3] in tests/integration/migrations/conftest.py)
# because this conftest is nested one level deeper:
# services/signal_gateway/tests/integration/conftest.py.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"
_TEST_HMAC_SECRET = "e2e-test-secret-padded-32chars!!"


class E2EFixture(NamedTuple):
    """Bundle yielded by :func:`webhook_e2e` — app under test plus subscriber sink."""

    app: FastAPI
    received: list[MessageEnvelope]


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    """Return a copy of ``dsn`` with the database name replaced."""
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


@pytest.fixture(scope="session")
def base_dsn() -> str:
    dsn = os.environ.get(_DSN_ENV_VAR)
    if not dsn:
        pytest.skip(
            f"{_DSN_ENV_VAR} not set — signal-gateway e2e tests require a "
            f"reachable PostgreSQL + TimescaleDB (see .env.example). "
            f"T-016 will wire testcontainers to set this in CI-full.",
            allow_module_level=True,
        )
    return dsn


@pytest.fixture(scope="session")
def nats_test_url() -> str:
    url = os.environ.get(_NATS_ENV_VAR)
    if not url:
        pytest.skip(
            f"{_NATS_ENV_VAR} not set — signal-gateway e2e tests require a "
            f"reachable NATS JetStream server with the SIGNALS stream "
            f"bootstrapped. T-016 will wire testcontainers to set this in CI-full.",
            allow_module_level=True,
        )
    return url


@pytest.fixture
async def migrated_db_dsn(base_dsn: str) -> AsyncIterator[str]:
    """Create a throwaway DB, run migrations, yield its DSN, drop after.

    Mirrors ``tests/integration/migrations/conftest.py``. Inline duplicate
    rather than imported because cross-directory fixture sharing in
    pytest requires either ``pytest_plugins`` or a shared root conftest;
    duplication is cheaper than either at F0 with two integration
    suites.
    """
    throwaway_name = f"scalper_v2_e2e_{uuid.uuid4().hex[:12]}"
    admin_conn = await asyncpg.connect(dsn=base_dsn)
    try:
        await admin_conn.execute(f'CREATE DATABASE "{throwaway_name}"')
    finally:
        await admin_conn.close()

    throwaway_dsn = _swap_database_in_dsn(base_dsn, throwaway_name)

    try:
        # Subprocess + worker thread mirrors the migrations conftest
        # rationale: migrations/env.py uses asyncio.run() which clashes
        # with pytest-asyncio's running loop if invoked in-process.
        await asyncio.to_thread(
            subprocess.run,
            ["uv", "run", "alembic", "-c", str(_ALEMBIC_INI), "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, _ALEMBIC_URL_ENV_VAR: throwaway_dsn},
            cwd=_REPO_ROOT,
        )
        yield throwaway_dsn
    finally:
        admin_conn = await asyncpg.connect(dsn=base_dsn)
        try:
            await admin_conn.execute(
                f'DROP DATABASE IF EXISTS "{throwaway_name}" WITH (FORCE)',
            )
        finally:
            await admin_conn.close()


@pytest.fixture
async def webhook_e2e(
    migrated_db_dsn: str,
    nats_test_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[E2EFixture]:
    """Return :class:`E2EFixture` — app + subscriber sink for ``signals.validated``.

    Order — load-bearing:

    1. Patch env vars (``DATABASE_URL`` → throwaway DSN, ``NATS_URL`` →
       test server, ``SIGNAL_GATEWAY_HMAC_SECRET`` → known value).
    2. Start subscriber :class:`NatsClient`: ``connect()`` then
       ``subscribe("signals.validated", handler)``.
    3. Build :class:`FastAPI` via :func:`create_app` — its lifespan
       does NOT run until the test enters ``TestClient(app)``.
    4. Yield. The test enters TestClient, lifespan publishes,
       subscriber's handler appends to ``received``.
    5. Teardown closes the subscriber regardless of test outcome.
    """
    monkeypatch.setenv("DATABASE_URL", migrated_db_dsn)
    monkeypatch.setenv("NATS_URL", nats_test_url)
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", _TEST_HMAC_SECRET)

    received: list[MessageEnvelope] = []

    async def handler(envelope: MessageEnvelope) -> None:
        received.append(envelope)

    subscriber_logger = get_logger("test-subscriber", "system")
    subscriber = NatsClient(
        servers=[nats_test_url],
        name=f"test-subscriber-{uuid.uuid4().hex[:8]}",
        logger=subscriber_logger,
    )
    await subscriber.connect()
    try:
        await subscriber.subscribe("signals.validated", handler)
        app = create_app()
        yield E2EFixture(app=app, received=received)
    finally:
        await subscriber.close()
