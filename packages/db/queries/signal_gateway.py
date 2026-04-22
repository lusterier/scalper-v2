"""signal-gateway query module (§5.10, §7.2).

Owned by ``services/signal_gateway``; imported by the T-015b2 handler for
symbol-map lookup and signals-table insert. Raw asyncpg per brief §5.10
("all queries in hot paths are raw SQL via asyncpg, parameterized").
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from typing import Any

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    # asyncpg-stubs splits the pool-acquired connection from a raw
    # asyncpg.connect() result into nominally-distinct classes that
    # share a structural query surface. Accept either so callers can
    # pass `async with pool.acquire() as conn` results without casting.
    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = ["fetch_symbol_mapping", "insert_signal"]


async def fetch_symbol_mapping(
    conn: _DbExecutor,
    input_symbol: str,
) -> str | None:
    """Return the canonical Bybit symbol for a TradingView input, or ``None`` if not mapped.

    Hits the ``symbol_map`` table (seeded in migration 0001). Callers wrap
    this in :class:`services.signal_gateway.app.symbol_map.SymbolMapCache`
    for 60 s in-process caching per §9.1 step 6.
    """
    row = await conn.fetchrow(
        "SELECT canonical_symbol FROM symbol_map WHERE input_symbol = $1",
        input_symbol,
    )
    if row is None:
        return None
    value = row["canonical_symbol"]
    return str(value) if value is not None else None


async def insert_signal(
    conn: _DbExecutor,
    *,
    received_at: datetime,
    schema_version: str,
    source: str,
    idempotency_key: str,
    symbol: str,
    original_symbol: str | None,
    action: str,
    payload: dict[str, Any],
    ingestion_status: str,
    correlation_id: str,
) -> int:
    """Insert one row into ``signals`` and return the generated ``id``.

    Column order + types match migration 0002 (§7.2 signals DDL). The
    ``payload`` dict is serialised via :func:`json.dumps` and cast to
    ``jsonb`` server-side; the default asyncpg codec map does not
    auto-convert Python dicts to ``jsonb``.

    ``ingestion_status`` is one of ``{"validated", "invalid", "duplicate"}``;
    caller owns the choice per the §9.1 pipeline outcome. A repeat
    ``(idempotency_key, received_at)`` tuple raises
    :class:`asyncpg.UniqueViolationError` via the ``signals_idempotency``
    unique index — the T-015b2 dedup ring is expected to have
    short-circuited that branch, so a violation here is bug-level.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO signals (
            received_at, schema_version, source, idempotency_key,
            symbol, original_symbol, action, payload,
            ingestion_status, correlation_id
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
        RETURNING id
        """,
        received_at,
        schema_version,
        source,
        idempotency_key,
        symbol,
        original_symbol,
        action,
        json.dumps(payload),
        ingestion_status,
        correlation_id,
    )
    if row is None:
        msg = "INSERT ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])
