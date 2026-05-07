"""signal-gateway query module (§5.10, §7.2).

Owned by ``services/signal_gateway``; imported by the T-015b2 handler for
symbol-map lookup and signals-table insert. Raw asyncpg per brief §5.10
("all queries in hot paths are raw SQL via asyncpg, parameterized").

L-011 pre-emptive convention (T-520 cherry-pick 2026-05-07): ``payload``
JSONB serialised via ``json.dumps(_to_jsonable(payload))`` text-mode.
``_to_jsonable`` recursively pre-stringifies any UUID/datetime/Decimal
in the dict, so the outer ``json.dumps`` cannot ``TypeError`` even if
upstream caller paths inject those types. Today's signal-gateway service
does NOT register ``_register_jsonb_codec`` on its asyncpg pool —
plain ``json.dumps`` text-mode + ``$N::jsonb`` cast is the working path.
**Switch trigger when codec registers** (F5+ if signal-gateway needs
analytics-api-style codec for downstream readers): drop the outer
``json.dumps`` wrapper — pass ``_to_jsonable(payload)`` dict directly to
asyncpg; codec encoder serialises via its own ``json.dumps`` (single-encoded).
This eliminates the F4 E1 ``Object of type UUID is not JSON serializable``
regression class regardless of codec state. Mirrors T-510b shadow.py
L-011 B-mode + plan-reviewer's L-011 forward-pointer convention.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from packages.core import idempotent, non_idempotent

# L-011 pre-emptive convention helper. ``_to_jsonable`` is intentionally
# private (not in audit.py __all__) but is the canonical UUID/datetime/Decimal
# pre-stringifier across all packages.db.queries JSONB writers. T-510b
# precedent established option (c) explicit private-import via # noqa: PLC2701.
from packages.db.queries.audit import _to_jsonable

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

__all__ = [
    "fetch_symbol_mapping",
    "insert_signal",
    "select_signal_id_by_idempotency_key",
]


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


@non_idempotent
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

    Marked ``@non_idempotent`` per §N3 / §5.8: callers (T-015b2
    ``/webhook`` handler) do **not** retry on failure — a DB error
    surfaces as ``500 internal``, the operator / TradingView re-sends
    the webhook, and the row lands on the second attempt. The
    ``signals_idempotency`` unique index on ``(idempotency_key,
    received_at)`` is a separate dedup guard against double-writes
    within the same ``received_at`` instant; it is **not** retry
    safety and must not be relied on for that purpose.

    Column order + types match migration 0002 (§7.2 signals DDL). The
    ``payload`` dict is serialised via ``json.dumps(_to_jsonable(payload))``
    text-mode and cast to ``jsonb`` server-side. ``_to_jsonable`` pre-
    stringifies any UUID/datetime/Decimal (defensive — webhook payloads
    are typically JSON-native dicts, but the wrapper is L-011 pre-emptive
    in case upstream caller paths inject typed values). See module
    docstring for switch trigger if signal-gateway later registers a
    JSONB codec on its asyncpg pool.

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
        json.dumps(_to_jsonable(payload)),
        ingestion_status,
        correlation_id,
    )
    if row is None:
        msg = "INSERT ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])


@idempotent
async def select_signal_id_by_idempotency_key(
    conn: _DbExecutor,
    *,
    idempotency_key: str,
    received_at_lower_bound: datetime,
) -> int | None:
    """Return ``signals.id`` for a previously-inserted webhook, or ``None``.

    Used by strategy-engine consumer (T-310b) to resolve ``signal_id: int``
    from ``SignalValidated.idempotency_key: str`` (schema does not carry
    the surrogate id; OQ-1 Path A operator-decision 2026-05-02).

    Requires ``received_at_lower_bound`` for hypertable chunk pruning —
    Timescale needs the partitioning column in WHERE for chunk-skip per
    `services/feature_engine/app/yaml_loader.py` precedent. Caller passes
    ``now - max_signal_age_seconds`` (T-310b default 600s).

    The ``signals_idempotency`` UNIQUE composite index on
    ``(idempotency_key, received_at)`` (migration 0002) supports the
    range scan; ``ORDER BY received_at DESC LIMIT 1`` is the index-scan-
    backwards top-K idiom (returns the most recent match if duplicate
    keys span the window — defensive; the UNIQUE constraint prevents
    duplicates within a single ``received_at`` instant).
    """
    row = await conn.fetchrow(
        """
        SELECT id FROM signals
        WHERE idempotency_key = $1
          AND received_at >= $2
        ORDER BY received_at DESC
        LIMIT 1
        """,
        idempotency_key,
        received_at_lower_bound,
    )
    return int(row["id"]) if row is not None else None
