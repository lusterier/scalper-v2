"""Historical signal replay source (T-504 / brief §12.2:1956).

Async-iterable cursor-streamed replay of ``signals`` table rows for a
bot's symbol universe within a closed-open time window. Yields
:class:`packages.db.queries.analytics.SignalRow` instances ordered by
``received_at ASC``. Filtered by ``ingestion_status='validated'`` only —
duplicate / invalid signals don't replay (T-513 rejected-signal
tracking is a separate replay path).

Read-only consumer; no writes. Pace control (1x / 10x / max per BRIEF
§12.2:1955) lives in **T-502 ReplayBus** — T-504 yields rows fast as
the DB returns them; T-502 paces consumption. Separation of concerns
per OQ-3=A.

Cursor + transaction lifetime caveat: ``async for row in conn.cursor(...)``
consumes within the wrapping ``conn.transaction()`` block — once
iteration starts, the pool connection is held from the first yield
until iterator exhaustion or consumer error. Acceptable for T-507 CLI
single-bot replay (one connection, no contention; pool size of 1
sufficient). T-509 worker concurrency (multiple parallel backtests in
analytics-api lifespan) MUST size the pool accordingly — flagged
out-of-scope for T-504 but visible here so future T-509 plan-doc
sees the constraint.

L-011 read-side robustness: ``payload`` is JSONB. analytics-api lifespan
registers ``_register_jsonb_codec`` (decoder = ``json.loads``) → reads
return ``dict``. T-507 CLI process does NOT register the codec → reads
return raw ``str``. Defensive decode handles both: if ``str``, parse
via ``json.loads``; if ``dict``, pass through. Mirror precedent:
``packages/db/queries/audit.py`` post-2026-05-07 fix + T-501
``test_0013_meta_default_is_empty_jsonb`` explicit ``json.loads`` decode.

Boundary-check belt-and-suspenders (mirror T-505 module docstring 19-26):
constructor raises ``ValueError`` on empty ``symbol_universe`` and
``to_at <= from_at``. T-507 CLI is the upstream user-input boundary;
T-504's runtime checks are belt-and-suspenders during F5 build-up.
T-519 hazard audit may reassess if upstream T-507 already enforces.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from packages.core.types import Action, IngestionStatus
from packages.db.queries.analytics import SignalRow

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    import asyncpg


__all__ = ["HistoricalSignalSource"]


# Asyncpg-cursor chunk size; infra tuning constant, NOT a business knob (no
# observable production behavior beyond memory footprint). §N9-exempt per L-001
# active control: "polling/timing/pacing constants in BUSINESS LOGIC violate §N9
# even if sensible defaults" — cursor prefetch is infrastructure, not business
# logic. Unexposed to constructor per §0.8 anti-hypothetical-knob discipline.
_DEFAULT_PREFETCH = 1000

_SELECT_SIGNALS_REPLAY_SQL = """
    SELECT id, received_at, schema_version, source, idempotency_key,
           symbol, original_symbol, action, payload, ingestion_status,
           correlation_id
    FROM signals
    WHERE symbol = ANY($1)
      AND received_at >= $2
      AND received_at < $3
      AND ingestion_status = 'validated'
    ORDER BY received_at ASC
"""


def _row_to_signal_replay(row: asyncpg.Record) -> SignalRow:
    """Narrow asyncpg row to typed :class:`SignalRow` with defensive JSONB decode.

    L-011 read-side: ``payload`` JSONB returns as ``str`` without registered
    codec (T-507 CLI process) and as ``dict`` with codec (analytics-api). Both
    paths land at dict shape via this branch.
    """
    payload_raw = row["payload"]
    payload: dict[str, Any] = (
        json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    )
    original_symbol = row["original_symbol"]
    return SignalRow(
        id=int(row["id"]),
        received_at=row["received_at"],
        schema_version=str(row["schema_version"]),
        source=str(row["source"]),
        idempotency_key=str(row["idempotency_key"]),
        symbol=str(row["symbol"]),
        original_symbol=str(original_symbol) if original_symbol is not None else None,
        action=Action(str(row["action"])),
        payload=payload,
        ingestion_status=IngestionStatus(str(row["ingestion_status"])),
        correlation_id=str(row["correlation_id"]),
    )


class HistoricalSignalSource:
    """Async-iterable cursor-streamed historical signal replay.

    See module docstring for full algorithm + L-011 read-side robustness +
    cursor+tx lifetime caveat + boundary-check belt-and-suspenders rationale.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        bot_id: str,
        symbol_universe: list[str],
        from_at: datetime,
        to_at: datetime,
    ) -> None:
        if not symbol_universe:
            msg = "HistoricalSignalSource: symbol_universe must not be empty"
            raise ValueError(msg)
        if to_at <= from_at:
            msg = (
                f"HistoricalSignalSource: to_at must be > from_at "
                f"(from_at={from_at!r}, to_at={to_at!r})"
            )
            raise ValueError(msg)
        self._pool = pool
        self._bot_id = bot_id
        self._symbol_universe = list(symbol_universe)
        self._from_at = from_at
        self._to_at = to_at

    async def __aiter__(self) -> AsyncIterator[SignalRow]:
        async with self._pool.acquire() as conn, conn.transaction():
            async for row in conn.cursor(
                _SELECT_SIGNALS_REPLAY_SQL,
                self._symbol_universe,
                self._from_at,
                self._to_at,
                prefetch=_DEFAULT_PREFETCH,
            ):
                yield _row_to_signal_replay(row)
