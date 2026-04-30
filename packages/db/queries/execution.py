"""execution-service query module (§5.10, §7.2).

Owned by ``services/execution`` (T-215+); imported by the T-215 adapter
pool composition root for the active-bots read at lifespan startup.

The bots-table column set (§7.2 line 846-859) is read read-only here:
``bot_id``, ``display_name``, ``exchange_mode``. Filtered by
``status='active'`` so paused/archived bots don't enter the adapter
pool. Result is sorted by ``bot_id`` so partial-failure during
:func:`services.execution.app.pool.build_adapter_pool` always surfaces
the same first-failed bot on repeated restarts (debugability +
reproducible-failure invariant per T-215 plan-doc Edge case #5 / WG#3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = ["BotRow", "ExchangeMode", "select_active_bots"]


ExchangeMode = Literal["live", "testnet", "paper"]
_VALID_EXCHANGE_MODES: frozenset[str] = frozenset({"live", "testnet", "paper"})

_SELECT_ACTIVE_BOTS_SQL = """
    SELECT bot_id, display_name, exchange_mode
    FROM bots
    WHERE status = 'active'
    ORDER BY bot_id
"""


@dataclass(frozen=True, slots=True)
class BotRow:
    """Read-only projection of ``bots`` table columns needed for adapter composition."""

    bot_id: str
    display_name: str
    exchange_mode: ExchangeMode


def _validate_exchange_mode(value: str) -> ExchangeMode:
    """Narrow DB Text column to ``ExchangeMode`` literal; raise on unknown.

    Defends against operator typos in the bots table — unknown modes
    crash composition rather than silently route to an undefined branch.
    """
    if value not in _VALID_EXCHANGE_MODES:
        raise ValueError(
            f"unknown exchange_mode {value!r}; expected one of {sorted(_VALID_EXCHANGE_MODES)}"
        )
    return value  # type: ignore[return-value]


async def select_active_bots(conn: _DbExecutor) -> list[BotRow]:
    """Return active bots ordered by ``bot_id`` (deterministic for partial-failure debugability)."""
    rows = await conn.fetch(_SELECT_ACTIVE_BOTS_SQL)
    return [
        BotRow(
            bot_id=str(row["bot_id"]),
            display_name=str(row["display_name"]),
            exchange_mode=_validate_exchange_mode(str(row["exchange_mode"])),
        )
        for row in rows
    ]
