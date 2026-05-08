"""Fixtures for execution-service integration tests (T-512b).

Spins up a throwaway PostgreSQL database per test (migrated via
``alembic upgrade head``, mirroring
``services/signal_gateway/tests/integration/conftest.py``) and yields
real :class:`asyncpg.Pool` + connected :class:`packages.bus.NatsClient`
instances scoped to the test.

DSN comes from ``POSTGRES_TEST_DSN`` and the NATS server URL from
``NATS_TEST_URL``. If either is unset, every test in this directory
is skipped at module collection time — keeping CI-fast green until
T-016 testcontainer wiring lights both env vars.

T-512b verifies BRIEF §13.4 / §19:2589 / §20:2787 shadow-variant
restart-recovery via OHLC replay. Tests simulate execution-service
restart in-process (operator OQ-1=A 2026-05-08; mirror existing repo
integration patterns vs. novel subprocess+SIGTERM infra).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from packages.bus import NatsClient
from packages.db import create_pool
from packages.observability import get_logger
from services.execution.app.config import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_DSN_ENV_VAR = "POSTGRES_TEST_DSN"
_NATS_ENV_VAR = "NATS_TEST_URL"
_ALEMBIC_URL_ENV_VAR = "POSTGRES_URL"
# parents[4]: services/execution/tests/integration/conftest.py → repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


@pytest.fixture(scope="session")
def base_dsn() -> str:
    dsn = os.environ.get(_DSN_ENV_VAR)
    if not dsn:
        pytest.skip(
            f"{_DSN_ENV_VAR} not set — execution-service shadow-restart e2e tests "
            f"require a reachable PostgreSQL + TimescaleDB. T-016 wires testcontainers "
            f"in CI-full.",
            allow_module_level=True,
        )
    return dsn


@pytest.fixture(scope="session")
def nats_test_url() -> str:
    url = os.environ.get(_NATS_ENV_VAR)
    if not url:
        pytest.skip(
            f"{_NATS_ENV_VAR} not set — execution-service shadow-restart e2e tests "
            f"require a reachable NATS JetStream server. T-016 wires testcontainers "
            f"in CI-full.",
            allow_module_level=True,
        )
    return url


@pytest.fixture
async def migrated_db_dsn(base_dsn: str) -> AsyncIterator[str]:
    """Create a throwaway DB, run migrations, yield its DSN, drop after.

    Mirrors ``services/signal_gateway/tests/integration/conftest.py:migrated_db_dsn``
    verbatim. Inline duplicate rather than imported because cross-directory
    fixture sharing in pytest requires either ``pytest_plugins`` or a shared
    root conftest; duplication is cheaper than either at this scope.
    """
    throwaway_name = f"scalper_v2_exec_{uuid.uuid4().hex[:12]}"
    admin_conn = await asyncpg.connect(dsn=base_dsn)
    try:
        await admin_conn.execute(f'CREATE DATABASE "{throwaway_name}"')
    finally:
        await admin_conn.close()

    throwaway_dsn = _swap_database_in_dsn(base_dsn, throwaway_name)

    try:
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
def executions_settings(
    migrated_db_dsn: str,
    nats_test_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Settings:
    """:class:`Settings` populated from migrated DB + NATS test URL."""
    monkeypatch.setenv("DATABASE_URL", migrated_db_dsn)
    monkeypatch.setenv("NATS_URL", nats_test_url)
    return Settings()  # type: ignore[call-arg]


@pytest.fixture
async def pool(migrated_db_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    """Connected :class:`asyncpg.Pool` against the throwaway test DB."""
    p = await create_pool(migrated_db_dsn, application_name="execution-test")
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def bus(nats_test_url: str) -> AsyncIterator[NatsClient]:
    """Connected :class:`NatsClient`. Per-test unique connection name avoids
    orphan-consumer accumulation across the suite.
    """
    bus_logger = get_logger("test-execution-integration", "system")
    client = NatsClient(
        servers=[nats_test_url],
        name=f"test-execution-{uuid.uuid4().hex[:8]}",
        logger=bus_logger,
    )
    await client.connect()
    try:
        yield client
    finally:
        await client.close()
