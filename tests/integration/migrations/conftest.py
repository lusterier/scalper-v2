"""Fixtures for per-migration integration tests (brief §N8, §7.4).

The fixtures here create a throwaway PostgreSQL database per test run,
run ``alembic upgrade head`` against it, and drop it at teardown. This
is what §7.4 calls "runs up on an empty DB" — we cannot reuse the dev
database because ``CREATE EXTENSION timescaledb`` is DB-scoped and
subsequent hypertable migrations expect a clean starting schema.

The DSN comes from the ``POSTGRES_TEST_DSN`` environment variable. If
unset, every test in ``tests/integration/migrations/`` is skipped at
collection time — this keeps CI-fast (which does not stand up a
Postgres) green while still satisfying §N8's "accompanying test
exists" literal. T-016 wires testcontainers to set this var for
CI-full.

Migrations run via the ``alembic`` CLI in a subprocess rather than
``alembic.command.upgrade`` in-process: ``migrations/env.py`` ends with
``asyncio.run(run_migrations_online())``, which cannot be called from
inside the running event loop that pytest-asyncio installs for
``async def`` fixtures. The subprocess path also matches the
production flow described in §18.5 ("one-shot container runs
``alembic upgrade head``"), so the test exercises the real deploy
invocation rather than a Python-API bypass.

The test role needs ``CREATEDB`` privilege. On the dev-compose postgres
the ``scalper`` superuser has it; on testcontainers the default role
is a superuser.
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
_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _REPO_ROOT / "migrations" / "alembic.ini"


def _swap_database_in_dsn(dsn: str, new_dbname: str) -> str:
    """Return a copy of ``dsn`` with the database name replaced."""
    parsed = urlparse(dsn)
    return urlunparse(parsed._replace(path=f"/{new_dbname}"))


@pytest.fixture(scope="session")
def base_dsn() -> str:
    dsn = os.environ.get(_DSN_ENV_VAR)
    if not dsn:
        pytest.skip(
            f"{_DSN_ENV_VAR} not set — migration integration tests require a "
            f"reachable PostgreSQL + TimescaleDB (see .env.example). "
            f"T-016 will wire testcontainers to set this in CI-full.",
            allow_module_level=True,
        )
    return dsn


@pytest.fixture
async def migrated_db_dsn(base_dsn: str) -> AsyncIterator[str]:
    """Create a throwaway DB, run migrations, yield its DSN, drop after."""
    throwaway_name = f"scalper_v2_mig_{uuid.uuid4().hex[:12]}"
    admin_conn = await asyncpg.connect(dsn=base_dsn)
    try:
        await admin_conn.execute(f'CREATE DATABASE "{throwaway_name}"')
    finally:
        await admin_conn.close()

    throwaway_dsn = _swap_database_in_dsn(base_dsn, throwaway_name)

    try:
        # Offload the blocking subprocess call to a worker thread so it
        # does not stall pytest-asyncio's event loop (ASYNC221). The
        # CLI-via-subprocess path is deliberate: migrations/env.py ends
        # with `asyncio.run(run_migrations_online())`, which would clash
        # with the running loop if invoked as `alembic.command.upgrade`
        # in-process, and §18.5 describes prod deploy as a one-shot
        # container running the same `alembic upgrade head` command.
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
            # FORCE disconnects any lingering sessions (e.g. a connection
            # pool that did not fully close yet).
            await admin_conn.execute(f'DROP DATABASE IF EXISTS "{throwaway_name}" WITH (FORCE)')
        finally:
            await admin_conn.close()
