"""Exception hierarchy for :mod:`packages.db` (§5.4).

All errors raised by this package inherit from :class:`DatabaseError`,
which itself inherits from :class:`packages.core.ScalperError`, so
callers can narrow to ``except DatabaseError`` or broaden to
``except ScalperError`` without importing asyncpg.
"""

from __future__ import annotations

from packages.core import ScalperError

__all__ = ["DatabaseError", "InvalidDsnError"]


class DatabaseError(ScalperError):
    """Base class for errors raised by :mod:`packages.db`."""


class InvalidDsnError(DatabaseError):
    """DSN does not use an accepted PostgreSQL scheme."""
