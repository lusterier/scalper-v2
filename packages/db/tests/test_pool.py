"""Unit tests for :func:`packages.db.create_pool`.

No real PostgreSQL server is involved: ``asyncpg.create_pool`` is
mocked. Integration tests against a real Postgres container land with
T-009 + T-016 (testcontainers in CI-full).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from packages.db import create_pool
from packages.db.errors import InvalidDsnError

_PATCH_TARGET = "packages.db.pool.asyncpg.create_pool"


async def test_accepts_postgresql_scheme() -> None:
    with patch(_PATCH_TARGET, new=AsyncMock()) as mock_create:
        await create_pool("postgresql://u@h/d", application_name="svc")
    mock_create.assert_awaited_once()


async def test_accepts_postgres_scheme() -> None:
    with patch(_PATCH_TARGET, new=AsyncMock()) as mock_create:
        await create_pool("postgres://u@h/d", application_name="svc")
    mock_create.assert_awaited_once()


async def test_rejects_mysql_scheme() -> None:
    with pytest.raises(InvalidDsnError):
        await create_pool("mysql://u@h/d", application_name="svc")


async def test_rejects_http_scheme() -> None:
    with pytest.raises(InvalidDsnError):
        await create_pool("http://u@h/d", application_name="svc")


async def test_rejects_missing_scheme() -> None:
    with pytest.raises(InvalidDsnError):
        await create_pool("host/db", application_name="svc")


async def test_rejects_empty_dsn() -> None:
    with pytest.raises(InvalidDsnError):
        await create_pool("", application_name="svc")


async def test_invalid_dsn_error_does_not_leak_credentials() -> None:
    """Malformed DSN with embedded secret must not appear in the exception."""
    with pytest.raises(InvalidDsnError) as exc_info:
        await create_pool(
            "http://user:supersecret@host:5432/db",
            application_name="svc",
        )
    msg = str(exc_info.value)
    assert "supersecret" not in msg
    assert "user" not in msg


async def test_defaults_passed_to_asyncpg() -> None:
    with patch(_PATCH_TARGET, new=AsyncMock()) as mock_create:
        await create_pool("postgresql://u@h/d", application_name="svc")
    mock_create.assert_awaited_once_with(
        dsn="postgresql://u@h/d",
        min_size=2,
        max_size=10,
        command_timeout=30.0,
        server_settings={"application_name": "svc"},
        init=None,
    )


async def test_custom_values_passed_to_asyncpg() -> None:
    with patch(_PATCH_TARGET, new=AsyncMock()) as mock_create:
        await create_pool(
            "postgresql://u@h/d",
            application_name="execution",
            min_size=5,
            max_size=20,
            command_timeout=60.0,
        )
    mock_create.assert_awaited_once_with(
        dsn="postgresql://u@h/d",
        min_size=5,
        max_size=20,
        command_timeout=60.0,
        server_settings={"application_name": "execution"},
        init=None,
    )


async def test_init_callback_passed_to_asyncpg() -> None:
    """``init`` callback is forwarded to asyncpg.create_pool verbatim (T-110d)."""

    async def _codec_init(conn: object) -> None: ...

    with patch(_PATCH_TARGET, new=AsyncMock()) as mock_create:
        await create_pool(
            "postgresql://u@h/d",
            application_name="feature-engine",
            init=_codec_init,
        )
    mock_create.assert_awaited_once_with(
        dsn="postgresql://u@h/d",
        min_size=2,
        max_size=10,
        command_timeout=30.0,
        server_settings={"application_name": "feature-engine"},
        init=_codec_init,
    )


async def test_application_name_required_keyword() -> None:
    """application_name has no default — enforced at the type system level."""
    with patch(_PATCH_TARGET, new=AsyncMock()), pytest.raises(TypeError):
        await create_pool("postgresql://u@h/d")  # type: ignore[call-arg]


async def test_returns_pool_from_asyncpg() -> None:
    """The factory returns whatever ``asyncpg.create_pool`` returned."""
    sentinel = object()
    with patch(_PATCH_TARGET, new=AsyncMock(return_value=sentinel)):
        result = await create_pool("postgresql://u@h/d", application_name="svc")
    assert result is sentinel
