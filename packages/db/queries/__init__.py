"""Service-owned query modules (§5.10).

Common queries live in this package, one module per owning service
(``queries/signal_gateway.py``, ``queries/execution.py``, …). Each
module is authored by the service that owns the read/write path, so
schema evolution stays local to its owner.

No shared base classes live here; asyncpg's connection API is the
contract::

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT ... WHERE id = $1", order_id)

Per-service modules are added as services adopt the pool (F1+); this
F0 skeleton ships the namespace only.
"""

from __future__ import annotations

__all__: list[str] = []
