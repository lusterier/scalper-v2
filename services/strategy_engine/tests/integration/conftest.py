"""Fixtures for strategy-engine integration tests (T-526).

Spins up a throwaway PostgreSQL database per test (migrated via
``alembic upgrade head``, mirror
``services/execution/tests/integration/conftest.py`` shipped T-512b).
DSN comes from ``POSTGRES_TEST_DSN`` env var; if unset, every test in
this directory is skipped at module-collection time per the established
env-gated pattern.

T-526 cooldown gate is bus-free (derived-from-trades design per OQ-1=A);
no NATS fixture needed here. Future tasks adding full consumer end-to-end
tests should extend this conftest with a ``bus`` fixture mirror T-512b.
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

from packages.db import create_pool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_DSN_ENV_VAR = "POSTGRES_TEST_DSN"
_ALEMBIC_URL_ENV_VAR = "POSTGRES_URL"
# parents[4]: services/strategy_engine/tests/integration/conftest.py → repo root.
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
            f"{_DSN_ENV_VAR} not set — strategy-engine integration tests require "
            f"a reachable PostgreSQL + TimescaleDB. T-016 wires testcontainers in CI-full.",
            allow_module_level=True,
        )
    return dsn


@pytest.fixture
async def migrated_db_dsn(base_dsn: str) -> AsyncIterator[str]:
    """Create a throwaway DB, run migrations, yield its DSN, drop after.

    Mirror :mod:`services.execution.tests.integration.conftest`
    ``migrated_db_dsn`` verbatim. Inline duplicate per the same rationale
    documented there (cross-directory fixture sharing is fragile).
    """
    throwaway_name = f"scalper_v2_strat_{uuid.uuid4().hex[:12]}"
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
async def pool(migrated_db_dsn: str) -> AsyncIterator[asyncpg.Pool]:
    """Connected :class:`asyncpg.Pool` against the throwaway test DB."""
    p = await create_pool(migrated_db_dsn, application_name="strategy-engine-test")
    try:
        yield p
    finally:
        await p.close()
