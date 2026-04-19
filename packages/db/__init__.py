"""Shared PostgreSQL access primitives (§5.10, §19 F0 bullet 6).

Ships the asyncpg pool factory and the (currently empty) namespace
where service-owned query modules land. No ORM, no query wrapper —
asyncpg's connection API is the contract per §5.10.
"""

from __future__ import annotations

from .errors import DatabaseError, InvalidDsnError
from .pool import create_pool

__all__ = [
    "DatabaseError",
    "InvalidDsnError",
    "create_pool",
]
