"""Fixtures for ``scripts/`` integration tests (T-112).

Re-uses the ``migrated_db_dsn`` fixture pattern from
``tests/integration/queries/conftest.py`` and
``tests/integration/migrations/conftest.py``: spins a throwaway
PostgreSQL database per test, runs ``alembic upgrade head``, yields
the DSN, drops at teardown. Skipped at collection when
``POSTGRES_TEST_DSN`` is unset.

Mirrors the existing pattern verbatim — see ``tests/integration/queries/
conftest.py`` for the full rationale (and TODO note about lifting the
shared fixture once a third integration sub-tree appears; T-112 is the
third, so the consolidation is now overdue but deferred to keep
T-112's scope tight).
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
# parents[3]: tests/integration/scripts/conftest.py → repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


@pytest.fixture(scope="session")
def base_dsn() -> str:
    dsn = os.environ.get(_DSN_ENV_VAR)
    if not dsn:
        pytest.skip(
            f"{_DSN_ENV_VAR} not set — backfill property test requires "
            f"a reachable PostgreSQL + TimescaleDB. T-016 wires testcontainers "
            f"in CI-full.",
            allow_module_level=True,
        )
    return dsn


@pytest.fixture
async def migrated_db_dsn(base_dsn: str) -> AsyncIterator[str]:
    """Create a throwaway DB, run migrations, yield its DSN, drop after."""
    throwaway_name = f"scalper_v2_backfill_{uuid.uuid4().hex[:12]}"
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
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{throwaway_name}" WITH (FORCE)')
        finally:
            await admin_conn.close()
