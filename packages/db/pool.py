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

import asyncpg

from .errors import InvalidDsnError

__all__ = ["create_pool"]


_ALLOWED_SCHEMES: tuple[str, ...] = ("postgresql://", "postgres://")


async def create_pool(
    dsn: str,
    *,
    application_name: str,
    min_size: int = 2,
    max_size: int = 10,
    command_timeout: float = 30.0,
) -> asyncpg.Pool:
    """Create an asyncpg connection pool for a PostgreSQL server.

    ``application_name`` is required (no default) so every pool is
    attributable in ``pg_stat_activity``. Pass the owning service's
    name, e.g. ``"signal-gateway"`` or ``"execution"``.

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
    )
