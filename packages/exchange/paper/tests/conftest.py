"""Fixtures for PaperExchange persistence integration tests (T-213b).

Mirror ``tests/integration/queries/conftest.py`` pattern: spins a
throwaway PostgreSQL database per test, runs ``alembic upgrade head``
against it, yields the DSN of the migrated DB, and drops at teardown.
Skipped at collection time when ``POSTGRES_TEST_DSN`` is unset (mirror
the migration-tests env-gate; ci-full sets it via T-016 service container).
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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_DSN_ENV_VAR = "POSTGRES_TEST_DSN"
_ALEMBIC_URL_ENV_VAR = "POSTGRES_URL"
# parents[4]: packages/exchange/paper/tests/conftest.py → repo root.
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
            f"{_DSN_ENV_VAR} not set — paper persistence integration tests "
            f"require a reachable PostgreSQL + TimescaleDB. T-016 wires "
            f"testcontainers in ci-full.",
            allow_module_level=True,
        )
    return dsn


@pytest.fixture
async def migrated_db_dsn(base_dsn: str) -> AsyncIterator[str]:
    """Create a throwaway DB, run migrations, yield DSN, drop on teardown."""
    throwaway_name = f"scalper_v2_paper_{uuid.uuid4().hex[:12]}"
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
        # T-113: drop ohlc_15m cagg auto-refresh policy in throwaway DB
        # (not strictly needed for paper tests, but mirror the queries
        # conftest pattern in case a paper test issues a manual cagg refresh).
        policy_conn = await asyncpg.connect(dsn=throwaway_dsn)
        try:
            await policy_conn.execute("SELECT remove_continuous_aggregate_policy('ohlc_15m')")
        finally:
            await policy_conn.close()
        yield throwaway_dsn
    finally:
        admin_conn = await asyncpg.connect(dsn=base_dsn)
        try:
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{throwaway_name}" WITH (FORCE)')
        finally:
            await admin_conn.close()
