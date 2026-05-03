"""analytics-api query module (§7.2, §9.6).

Owned by ``packages/db/queries``; consumed by analytics-api router
endpoints to read ``bots`` rows + read/write ``symbol_map`` rows. Raw
asyncpg per brief §5.10 ("all queries in hot paths are raw SQL via
asyncpg, parameterized").

T-401a half: ``select_all_bots`` + ``select_bot_by_id`` + ``BotDetailRow``.
T-401b extends: ``SymbolMapRow`` + 5 symbol_map functions
(``select_all_symbol_map_entries`` + ``select_symbol_map_entry`` +
``insert_symbol_map_entry`` + ``update_symbol_map_entry`` +
``delete_symbol_map_entry``). Write helpers ``@non_idempotent`` per §N3.

``BotStatus`` / ``ExchangeMode`` / ``ExchangeSource`` enum narrowing
uses canonical :mod:`packages.core.types` StrEnums; the StrEnum
constructor itself raises :class:`ValueError` on unknown values, so no
hand-rolled validator is needed (cleaner than promoting the private
``_validate_exchange_mode`` from :mod:`packages.db.queries.execution`
per T-401a WG#2 plan-reviewer alternative + T-401b WG#1 consistency).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.core import non_idempotent
from packages.core.types import BotStatus, ExchangeMode, ExchangeSource

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = [
    "BotDetailRow",
    "SymbolMapRow",
    "delete_symbol_map_entry",
    "insert_symbol_map_entry",
    "select_all_bots",
    "select_all_symbol_map_entries",
    "select_bot_by_id",
    "select_symbol_map_entry",
    "update_symbol_map_entry",
]


@dataclass(frozen=True, slots=True)
class BotDetailRow:
    """Full projection of ``bots`` row (8 columns per §7.2:846-859).

    Distinct from :class:`packages.db.queries.execution.BotRow` — that
    is a 3-column projection used by the execution-service adapter pool
    (filtered to status='active' only). Analytics-api needs the full
    row for the Settings UI bot registry view, including paused +
    archived bots.
    """

    bot_id: str
    display_name: str
    created_at: datetime
    status: BotStatus
    exchange_mode: ExchangeMode
    config_hash: str
    config_applied_at: datetime
    meta: dict[str, Any]


_SELECT_ALL_BOTS_SQL = """
    SELECT bot_id, display_name, created_at, status, exchange_mode,
           config_hash, config_applied_at, meta
    FROM bots
    ORDER BY bot_id
"""

_SELECT_BOT_BY_ID_SQL = """
    SELECT bot_id, display_name, created_at, status, exchange_mode,
           config_hash, config_applied_at, meta
    FROM bots
    WHERE bot_id = $1
"""


def _row_to_bot_detail(row: asyncpg.Record) -> BotDetailRow:
    """Narrow asyncpg row to typed dataclass; StrEnum ctors validate enums."""
    meta_value = row["meta"]
    return BotDetailRow(
        bot_id=str(row["bot_id"]),
        display_name=str(row["display_name"]),
        created_at=row["created_at"],
        status=BotStatus(str(row["status"])),
        exchange_mode=ExchangeMode(str(row["exchange_mode"])),
        config_hash=str(row["config_hash"]),
        config_applied_at=row["config_applied_at"],
        meta=meta_value if isinstance(meta_value, dict) else {},
    )


async def select_all_bots(conn: _DbExecutor) -> list[BotDetailRow]:
    """Return all bots (active + paused + archived); ORDER BY bot_id ASC."""
    rows = await conn.fetch(_SELECT_ALL_BOTS_SQL)
    return [_row_to_bot_detail(row) for row in rows]


async def select_bot_by_id(conn: _DbExecutor, bot_id: str) -> BotDetailRow | None:
    """Return one bot row by bot_id; ``None`` if missing."""
    row = await conn.fetchrow(_SELECT_BOT_BY_ID_SQL, bot_id)
    return _row_to_bot_detail(row) if row is not None else None


# ---------------------------------------------------------------------------
# T-401b — symbol_map CRUD (§7.2:1131-1138, §9.6:1632, §16.8:2261)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SymbolMapRow:
    """Full projection of ``symbol_map`` row (6 columns per §7.2:1131-1138)."""

    input_symbol: str
    canonical_symbol: str
    exchange_source: ExchangeSource
    notes: str | None
    created_at: datetime
    updated_at: datetime


_SELECT_ALL_SYMBOL_MAP_SQL = """
    SELECT input_symbol, canonical_symbol, exchange_source, notes,
           created_at, updated_at
    FROM symbol_map
    ORDER BY input_symbol
"""

_SELECT_SYMBOL_MAP_BY_PK_SQL = """
    SELECT input_symbol, canonical_symbol, exchange_source, notes,
           created_at, updated_at
    FROM symbol_map
    WHERE input_symbol = $1
"""

_INSERT_SYMBOL_MAP_SQL = """
    INSERT INTO symbol_map (input_symbol, canonical_symbol, exchange_source,
                            notes, created_at, updated_at)
    VALUES ($1, $2, $3, $4, $5, $6)
    RETURNING input_symbol, canonical_symbol, exchange_source, notes,
              created_at, updated_at
"""

_UPDATE_SYMBOL_MAP_SQL = """
    UPDATE symbol_map
    SET canonical_symbol = $2,
        exchange_source = $3,
        notes = $4,
        updated_at = $5
    WHERE input_symbol = $1
    RETURNING input_symbol, canonical_symbol, exchange_source, notes,
              created_at, updated_at
"""

_DELETE_SYMBOL_MAP_SQL = """
    DELETE FROM symbol_map WHERE input_symbol = $1
"""


def _row_to_symbol_map(row: asyncpg.Record) -> SymbolMapRow:
    """Narrow asyncpg row to typed dataclass; ExchangeSource ctor validates enum."""
    return SymbolMapRow(
        input_symbol=str(row["input_symbol"]),
        canonical_symbol=str(row["canonical_symbol"]),
        exchange_source=ExchangeSource(str(row["exchange_source"])),
        notes=str(row["notes"]) if row["notes"] is not None else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def select_all_symbol_map_entries(conn: _DbExecutor) -> list[SymbolMapRow]:
    """Return all symbol_map rows ordered by input_symbol ASC."""
    rows = await conn.fetch(_SELECT_ALL_SYMBOL_MAP_SQL)
    return [_row_to_symbol_map(row) for row in rows]


async def select_symbol_map_entry(
    conn: _DbExecutor,
    input_symbol: str,
) -> SymbolMapRow | None:
    """Return one symbol_map row by input_symbol PK; ``None`` if missing."""
    row = await conn.fetchrow(_SELECT_SYMBOL_MAP_BY_PK_SQL, input_symbol)
    return _row_to_symbol_map(row) if row is not None else None


@non_idempotent
async def insert_symbol_map_entry(
    conn: _DbExecutor,
    *,
    input_symbol: str,
    canonical_symbol: str,
    exchange_source: str,
    notes: str | None,
    created_at: datetime,
    updated_at: datetime,
) -> SymbolMapRow:
    """INSERT one row into ``symbol_map`` and RETURN it.

    Marked ``@non_idempotent`` per §N3. Raises
    :class:`asyncpg.UniqueViolationError` on duplicate ``input_symbol``
    PK — caller (router) catches and returns 409 Conflict.

    Caller passes ``now_fn()`` for both ``created_at`` and ``updated_at``
    to keep them identical at insert time per §N1 (no SQL ``NOW()``).
    """
    row = await conn.fetchrow(
        _INSERT_SYMBOL_MAP_SQL,
        input_symbol,
        canonical_symbol,
        exchange_source,
        notes,
        created_at,
        updated_at,
    )
    if row is None:
        msg = "INSERT ... RETURNING produced no row"
        raise RuntimeError(msg)
    return _row_to_symbol_map(row)


@non_idempotent
async def update_symbol_map_entry(
    conn: _DbExecutor,
    *,
    input_symbol: str,
    canonical_symbol: str,
    exchange_source: str,
    notes: str | None,
    updated_at: datetime,
) -> SymbolMapRow | None:
    """Full PUT semantics — overwrites canonical_symbol + exchange_source + notes + updated_at.

    PK ``input_symbol`` and ``created_at`` preserved. Returns updated
    row or ``None`` if no row matched (caller returns 404).

    Marked ``@non_idempotent`` per §N3.
    """
    row = await conn.fetchrow(
        _UPDATE_SYMBOL_MAP_SQL,
        input_symbol,
        canonical_symbol,
        exchange_source,
        notes,
        updated_at,
    )
    return _row_to_symbol_map(row) if row is not None else None


@non_idempotent
async def delete_symbol_map_entry(
    conn: _DbExecutor,
    input_symbol: str,
) -> bool:
    """DELETE one symbol_map row; returns ``True`` if deleted, ``False`` if not found.

    Marked ``@non_idempotent`` per §N3. asyncpg's ``execute`` returns
    ``"DELETE N"`` status string; helper parses N to bool.
    """
    status = await conn.execute(_DELETE_SYMBOL_MAP_SQL, input_symbol)
    # asyncpg execute returns e.g. "DELETE 1" or "DELETE 0".
    return status.endswith(" 1")
