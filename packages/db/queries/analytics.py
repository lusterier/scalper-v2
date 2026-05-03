"""analytics-api query module (§7.2, §9.6).

Owned by ``packages/db/queries``; consumed by analytics-api router
endpoints to read ``bots`` rows + (T-401b) write/read ``symbol_map``
rows. Raw asyncpg per brief §5.10 ("all queries in hot paths are raw
SQL via asyncpg, parameterized").

T-401a half: ``select_all_bots`` + ``select_bot_by_id`` + ``BotDetailRow``.
T-401b extends: ``SymbolMapRow`` + 5 symbol_map functions (CRUD).

``BotStatus`` and ``ExchangeMode`` enum narrowing uses
:class:`packages.core.types.BotStatus` + :class:`packages.core.types.ExchangeMode`
StrEnums — the canonical domain types. The StrEnum constructor itself
raises :class:`ValueError` on unknown values, so no hand-rolled
validator is needed (cleaner than promoting the private
``_validate_exchange_mode`` from :mod:`packages.db.queries.execution`
per T-401a WG#2 plan-reviewer alternative).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.core.types import BotStatus, ExchangeMode

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = ["BotDetailRow", "select_all_bots", "select_bot_by_id"]


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
