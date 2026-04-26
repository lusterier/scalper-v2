"""asyncpg connection pool factory (§5.10, §3.3).

Services compose their own Pydantic settings and unpack them into
:func:`create_pool` at startup. The returned :class:`asyncpg.Pool` is
consumed directly — this package does not wrap asyncpg's API
(§5.10: raw SQL via asyncpg is the contract).

The DSN scheme is validated up-front so a typo in configuration fails
at service boot rather than at first query. The validator reports only
the scheme prefix on rejection so credentials embedded in a malformed
DSN (``http://user:password@...``) cannot leak into logs or tracebacks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncpg

from .errors import InvalidDsnError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = ["create_pool"]


_ALLOWED_SCHEMES: tuple[str, ...] = ("postgresql://", "postgres://")


async def create_pool(
    dsn: str,
    *,
    application_name: str,
    min_size: int = 2,
    max_size: int = 10,
    command_timeout: float = 30.0,
    init: Callable[[asyncpg.Connection[asyncpg.Record]], Awaitable[None]] | None = None,
) -> asyncpg.Pool:
    """Create an asyncpg connection pool for a PostgreSQL server.

    ``application_name`` is required (no default) so every pool is
    attributable in ``pg_stat_activity``. Pass the owning service's
    name, e.g. ``"signal-gateway"`` or ``"execution"``.

    ``init`` is invoked for every connection acquired into the pool —
    used for per-connection codec registration. T-110b
    ``insert_feature(value_json=...)`` writes go through asyncpg's
    JSONB type which defaults to raw-string codec; T-110d's
    feature-engine lifespan passes a callback that registers
    :func:`json.dumps` / :func:`json.loads` so dict ↔ JSONB round-trips
    work without per-call codec registration. asyncpg invokes the
    callback synchronously after the connection establishes; failures
    propagate and prevent the pool from yielding the broken connection.

    Raises :class:`~packages.db.errors.InvalidDsnError` if the DSN does
    not start with ``postgresql://`` or ``postgres://``. Any asyncpg
    connection error (bad credentials, unreachable host, …) propagates
    from the underlying :func:`asyncpg.create_pool` call.
    """
    if not dsn.startswith(_ALLOWED_SCHEMES):
        scheme = dsn.split("://", 1)[0] if "://" in dsn else "<no scheme>"
        raise InvalidDsnError(f"dsn must use scheme in {_ALLOWED_SCHEMES}, got {scheme!r}")
    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
        server_settings={"application_name": application_name},
        # asyncpg-stubs typed init as a Protocol with a positional-only
        # `con` parameter; structural match with the equivalent Callable
        # is correct at runtime but trips mypy strict's nominal Protocol
        # check. The pool tests cover both default-None and pass-through.
        init=init,  # type: ignore[arg-type]
    )
