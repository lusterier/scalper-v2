"""Alembic environment for scalper-v2 (brief §N8, §7.4, §5.10, §5.11).

Async engine because the rest of the codebase is asyncpg-only (§3.1,
§5.10). `connection.run_sync(do_run_migrations)` is the standard
async-template bridge: Alembic's migration API is sync, so we run it
inside a greenlet spawned by SQLAlchemy's async adapter. The
``sqlalchemy[asyncio]`` extra in the dev dep group is what ships the
``greenlet`` runtime required for this.

DSN comes from the ``POSTGRES_URL`` environment variable — credentials
never land in ``alembic.ini`` (§5.11, §16). The URL is normalised to
the asyncpg driver (``postgresql+asyncpg://…``) so the same value that
services pass to ``packages.db.create_pool`` also works here.

``application_name='alembic'`` is pushed to the server via asyncpg's
``server_settings`` connect arg so ``pg_stat_activity`` distinguishes
migration sessions from service pools (§5.10 attribution principle,
mirrored from ``packages/db/pool.py``).

``target_metadata`` is ``None`` in this revision — migration 0001 is
hand-written DDL (``op.create_table``, ``op.execute`` for
``CREATE EXTENSION``). A SQLAlchemy Core metadata module under
``packages/db/`` will be added the first time autogenerate is actually
useful; introducing it preemptively for T-010 would be out-of-scope
tooling (§0.8).

Offline mode is wired too — Alembic's standard behaviour — but the
only supported path for this project is online against a real
PostgreSQL + TimescaleDB.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config

if config.config_file_name is not None:
    from logging.config import fileConfig

    fileConfig(config.config_file_name)

target_metadata = None

_ENV_DSN_VAR = "POSTGRES_URL"
_ASYNCPG_DRIVER_PREFIX = "postgresql+asyncpg://"


def _resolve_dsn() -> str:
    dsn = os.environ.get(_ENV_DSN_VAR)
    if not dsn:
        raise RuntimeError(
            f"{_ENV_DSN_VAR} is required to run migrations; "
            f"set it to a postgresql://… DSN before invoking alembic."
        )
    # Normalise the common `postgresql://` / `postgres://` to the asyncpg
    # driver dialect so the async engine factory picks it up. Leave
    # already-qualified URLs (`postgresql+asyncpg://…`) untouched.
    return re.sub(r"^postgres(?:ql)?://", _ASYNCPG_DRIVER_PREFIX, dsn, count=1)


def run_migrations_offline() -> None:
    """Run migrations against a URL without a live connection.

    The URL is emitted as SQL; no ``Engine`` is created. Useful for
    generating SQL scripts that a DBA can apply out-of-band. Not part
    of our standard flow but wired for completeness.
    """
    context.configure(
        url=_resolve_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live async engine.

    The engine is built from a minimal section of the Alembic config
    with ``sqlalchemy.url`` overridden to the resolved DSN. asyncpg's
    ``server_settings`` ships ``application_name='alembic'`` so every
    migration connection is attributable in ``pg_stat_activity``,
    mirroring ``packages/db/pool.py`` (§5.10).
    """
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_dsn()

    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"application_name": "alembic"}},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
