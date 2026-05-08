"""shadow query module (T-510b / brief §13.3 + §13.5).

Read + write helpers over T-510a ``shadow_variants`` + ``shadow_rejected``
tables. Mirrors :mod:`packages.db.queries.analytics` `BacktestRunRow`
+ `_row_to_*` + `_*_SQL` constants pattern + :mod:`packages.db.queries.audit`
``insert_audit_event`` ``@non_idempotent`` write convention.

Consumers:

* T-511 shadow-worker FSM — `insert_shadow_variant` + `update_shadow_variant_terminal`.
* T-512 OHLC replay restart-recovery — `select_active_shadow_variants(bot_id)`
  reads variants with ``terminated_at IS NULL`` for resume.
* T-513 rejected-signal 60-min observation — `insert_shadow_rejected` +
  `update_shadow_rejected_terminal` + `select_active_shadow_rejected(bot_id)`.
* T-516 UI per-trade variants drill-down — `select_shadow_variant_by_id`.
* T-517 aggregate + rejected explorer — `select_*_by_id`.

L-011 mode (operator-decision B = pre-emptive, plan-reviewer recommendation
2026-05-07): ``meta`` JSONB writes use ``json.dumps(_to_jsonable(meta))``
text-mode pattern. ``_to_jsonable`` (imported privately from
``packages.db.queries.audit``) recursively pre-stringifies UUID / datetime /
Decimal in the dict so the outer ``json.dumps`` cannot ``TypeError`` on
non-JSON-native types. The wrapper layout is constant across both codec
states; the **switch trigger** when execution-service registers
``_register_jsonb_codec`` (F5+ task) is to drop the outer ``json.dumps(...)``
wrapper — pass ``_to_jsonable(meta)`` dict directly to asyncpg, codec
encoder serializes via ``json.dumps`` internally (single-encoded). This
eliminates the F4 E1 ``TypeError: Object of type UUID is not JSON
serializable`` regression class regardless of codec state.

terminal_outcome row-decoder narrowing per operator-decision A (mirror
T-407 ``BacktestStatus(str(row["status"]))`` precedent at
``analytics.py:1639``): ``_row_to_*`` decoders narrow via
``ShadowVariantTerminal(str(row["terminal_outcome"]))`` if not None.
T-510a OQ-4=A guarantees no DB CHECK constraint on the column, so
forward-compat for ``replay-error`` / ``shutdown-mid-replay`` is by adding
a value to the StrEnum (no DB migration; app-layer only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from packages.core import non_idempotent
from packages.core.types import ShadowRejectedTerminal, ShadowVariantTerminal

# L-011 pre-emptive convention helper. ``_to_jsonable`` is intentionally
# private (not in audit.py __all__) but is the canonical UUID/datetime/Decimal
# pre-stringifier across all packages.db.queries JSONB writers. WG#3 chose
# explicit private-import (option c) over alternatives (extract to shared
# module / promote to public). See audit.py:55 docstring + module L-011 note.
from packages.db.queries.audit import _to_jsonable

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = [
    "ShadowRejectedRow",
    "ShadowVariantRow",
    "insert_shadow_rejected",
    "insert_shadow_variant",
    "select_active_shadow_rejected",
    "select_active_shadow_variants",
    "select_all_active_shadow_variants",
    "select_shadow_rejected_by_id",
    "select_shadow_variant_by_id",
    "update_shadow_rejected_terminal",
    "update_shadow_variant_terminal",
]


@dataclass(frozen=True, slots=True)
class ShadowVariantRow:
    """Full projection of ``shadow_variants`` row (15 cols per T-511b2a migration 0015).

    ``parent_kind`` discriminator (T-511b2a / ADR-0010) routes ``parent_trade_id``
    to either ``trades.id`` (live) or ``paper_trades.id`` (paper). Migration 0015
    drops the original 0014 FK; integrity at app layer via the discriminator.
    """

    id: int
    parent_trade_id: int
    bot_id: str
    variant_name: str
    side: str
    entry_price: Decimal
    qty: Decimal
    created_at: datetime
    terminated_at: datetime | None
    terminal_outcome: ShadowVariantTerminal | None
    realized_pnl: Decimal | None
    mfe_pct: float | None
    mae_pct: float | None
    meta: dict[str, Any]
    parent_kind: Literal["live", "paper"]


@dataclass(frozen=True, slots=True)
class ShadowRejectedRow:
    """Full projection of ``shadow_rejected`` row (11 cols per T-510a migration 0014)."""

    id: int
    signal_id: int
    bot_id: str
    symbol: str
    would_side: str
    created_at: datetime
    terminated_at: datetime | None
    terminal_outcome: ShadowRejectedTerminal | None
    mfe_pct: float | None
    mae_pct: float | None
    meta: dict[str, Any]


_SHADOW_VARIANT_BASE_COLUMNS = (
    "id, parent_trade_id, bot_id, variant_name, side, entry_price, qty, "
    "created_at, terminated_at, terminal_outcome, realized_pnl, "
    "mfe_pct, mae_pct, meta, parent_kind"
)

_SHADOW_REJECTED_BASE_COLUMNS = (
    "id, signal_id, bot_id, symbol, would_side, created_at, "
    "terminated_at, terminal_outcome, mfe_pct, mae_pct, meta"
)

_SELECT_ACTIVE_VARIANTS_SQL = (
    f"SELECT {_SHADOW_VARIANT_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM shadow_variants"
    " WHERE bot_id = $1 AND terminated_at IS NULL"
    " ORDER BY created_at ASC"
)

_SELECT_ALL_ACTIVE_VARIANTS_SQL = (
    f"SELECT {_SHADOW_VARIANT_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM shadow_variants"
    " WHERE terminated_at IS NULL"
    " ORDER BY created_at ASC"
)

_SELECT_ACTIVE_REJECTED_SQL = (
    f"SELECT {_SHADOW_REJECTED_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM shadow_rejected"
    " WHERE bot_id = $1 AND terminated_at IS NULL"
    " ORDER BY created_at ASC"
)

_SELECT_VARIANT_BY_ID_SQL = (
    f"SELECT {_SHADOW_VARIANT_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM shadow_variants WHERE id = $1"
)

_SELECT_REJECTED_BY_ID_SQL = (
    f"SELECT {_SHADOW_REJECTED_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM shadow_rejected WHERE id = $1"
)

_INSERT_SHADOW_VARIANT_SQL = """
    INSERT INTO shadow_variants (
        parent_trade_id, bot_id, variant_name, side,
        entry_price, qty, created_at, meta, parent_kind
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, COALESCE($8::jsonb, '{}'::jsonb), $9)
    RETURNING id, parent_trade_id, bot_id, variant_name, side, entry_price, qty,
              created_at, terminated_at, terminal_outcome, realized_pnl,
              mfe_pct, mae_pct, meta, parent_kind
"""

_INSERT_SHADOW_REJECTED_SQL = """
    INSERT INTO shadow_rejected (
        signal_id, bot_id, symbol, would_side, created_at, meta
    )
    VALUES ($1, $2, $3, $4, $5, COALESCE($6::jsonb, '{}'::jsonb))
    RETURNING id, signal_id, bot_id, symbol, would_side, created_at,
              terminated_at, terminal_outcome, mfe_pct, mae_pct, meta
"""

_UPDATE_VARIANT_TERMINAL_SQL = """
    UPDATE shadow_variants
       SET terminated_at = $2,
           terminal_outcome = $3,
           realized_pnl = $4,
           mfe_pct = $5,
           mae_pct = $6
     WHERE id = $1
    RETURNING id, parent_trade_id, bot_id, variant_name, side, entry_price, qty,
              created_at, terminated_at, terminal_outcome, realized_pnl,
              mfe_pct, mae_pct, meta, parent_kind
"""

_UPDATE_REJECTED_TERMINAL_SQL = """
    UPDATE shadow_rejected
       SET terminated_at = $2,
           terminal_outcome = $3,
           mfe_pct = $4,
           mae_pct = $5
     WHERE id = $1
    RETURNING id, signal_id, bot_id, symbol, would_side, created_at,
              terminated_at, terminal_outcome, mfe_pct, mae_pct, meta
"""


def _serialize_meta(meta: dict[str, Any] | None) -> str | None:
    """L-011 B-mode pre-emptive: pre-stringify via _to_jsonable, then json.dumps text-mode.

    Returns None when ``meta`` is None so SQL ``COALESCE($N::jsonb, '{}'::jsonb)``
    applies the column DEFAULT. **Switch trigger when codec registers**: drop
    the outer ``json.dumps`` and return the dict from ``_to_jsonable`` directly;
    asyncpg codec will encode via ``json.dumps`` internally.
    """
    if meta is None:
        return None
    return json.dumps(_to_jsonable(meta))


def _row_to_shadow_variant(row: asyncpg.Record) -> ShadowVariantRow:
    """Narrow asyncpg row to typed :class:`ShadowVariantRow`.

    NUMERIC columns pass-through Decimal (asyncpg native). DOUBLE PRECISION
    via ``float()``. ``terminal_outcome`` narrowed via :class:`ShadowVariantTerminal`
    StrEnum if not None (operator-decision A; T-407 BacktestStatus precedent).
    ``meta`` JSONB defensive dict-narrow.
    """
    meta_value = row["meta"]
    terminal_raw = row["terminal_outcome"]
    parent_kind_raw = str(row["parent_kind"])
    if parent_kind_raw not in ("live", "paper"):
        msg = f"shadow_variants.parent_kind unexpected value {parent_kind_raw!r}"
        raise ValueError(msg)
    return ShadowVariantRow(
        id=int(row["id"]),
        parent_trade_id=int(row["parent_trade_id"]),
        bot_id=str(row["bot_id"]),
        variant_name=str(row["variant_name"]),
        side=str(row["side"]),
        entry_price=row["entry_price"],
        qty=row["qty"],
        created_at=row["created_at"],
        terminated_at=row["terminated_at"],
        terminal_outcome=(
            ShadowVariantTerminal(str(terminal_raw)) if terminal_raw is not None else None
        ),
        realized_pnl=row["realized_pnl"],
        mfe_pct=float(row["mfe_pct"]) if row["mfe_pct"] is not None else None,
        mae_pct=float(row["mae_pct"]) if row["mae_pct"] is not None else None,
        meta=meta_value if isinstance(meta_value, dict) else {},
        parent_kind=parent_kind_raw,  # type: ignore[arg-type]
    )


def _row_to_shadow_rejected(row: asyncpg.Record) -> ShadowRejectedRow:
    """Narrow asyncpg row to typed :class:`ShadowRejectedRow`."""
    meta_value = row["meta"]
    terminal_raw = row["terminal_outcome"]
    return ShadowRejectedRow(
        id=int(row["id"]),
        signal_id=int(row["signal_id"]),
        bot_id=str(row["bot_id"]),
        symbol=str(row["symbol"]),
        would_side=str(row["would_side"]),
        created_at=row["created_at"],
        terminated_at=row["terminated_at"],
        terminal_outcome=(
            ShadowRejectedTerminal(str(terminal_raw)) if terminal_raw is not None else None
        ),
        mfe_pct=float(row["mfe_pct"]) if row["mfe_pct"] is not None else None,
        mae_pct=float(row["mae_pct"]) if row["mae_pct"] is not None else None,
        meta=meta_value if isinstance(meta_value, dict) else {},
    )


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def select_active_shadow_variants(
    conn: _DbExecutor,
    *,
    bot_id: str,
) -> list[ShadowVariantRow]:
    """Return active variants (terminated_at IS NULL) for ``bot_id`` ordered by created_at ASC.

    Uses ``shadow_variants_bot_active`` partial index (T-510a). T-516 UI
    per-trade variants drill-down consumer.
    """
    rows = await conn.fetch(_SELECT_ACTIVE_VARIANTS_SQL, bot_id)
    return [_row_to_shadow_variant(row) for row in rows]


async def select_all_active_shadow_variants(
    conn: _DbExecutor,
) -> list[ShadowVariantRow]:
    """Cross-bot enumeration of active variants ordered by created_at ASC (T-512a).

    Used by ``services.execution.app.shadow_replay.resume_active_variants_on_startup``
    on lifespan startup to iterate every shadow variant pending across all
    bots. Differs from :func:`select_active_shadow_variants` — no
    ``bot_id`` filter; sequential index scan on ``shadow_variants_bot_active``
    partial index (terminated_at IS NULL filter) acceptable at expected
    F5 scale (<500 active variants total per H-016 + T-510a docstring).
    """
    rows = await conn.fetch(_SELECT_ALL_ACTIVE_VARIANTS_SQL)
    return [_row_to_shadow_variant(row) for row in rows]


async def select_active_shadow_rejected(
    conn: _DbExecutor,
    *,
    bot_id: str,
) -> list[ShadowRejectedRow]:
    """Return active rejected-signal observations (terminated_at IS NULL) for ``bot_id``."""
    rows = await conn.fetch(_SELECT_ACTIVE_REJECTED_SQL, bot_id)
    return [_row_to_shadow_rejected(row) for row in rows]


async def select_shadow_variant_by_id(
    conn: _DbExecutor,
    *,
    variant_id: int,
) -> ShadowVariantRow | None:
    """Return ShadowVariantRow on hit; None on miss."""
    row = await conn.fetchrow(_SELECT_VARIANT_BY_ID_SQL, variant_id)
    return _row_to_shadow_variant(row) if row is not None else None


async def select_shadow_rejected_by_id(
    conn: _DbExecutor,
    *,
    rejected_id: int,
) -> ShadowRejectedRow | None:
    """Return ShadowRejectedRow on hit; None on miss."""
    row = await conn.fetchrow(_SELECT_REJECTED_BY_ID_SQL, rejected_id)
    return _row_to_shadow_rejected(row) if row is not None else None


# ---------------------------------------------------------------------------
# Write helpers (@non_idempotent per §N3)
# ---------------------------------------------------------------------------


@non_idempotent
async def insert_shadow_variant(
    conn: _DbExecutor,
    *,
    parent_trade_id: int,
    bot_id: str,
    variant_name: str,
    side: str,
    entry_price: Decimal,
    qty: Decimal,
    created_at: datetime,
    parent_kind: Literal["live", "paper"],
    meta: dict[str, Any] | None = None,
) -> ShadowVariantRow:
    """INSERT shadow_variants row; ``meta=None`` → SQL DEFAULT ``'{}'::jsonb`` applies.

    ``parent_kind`` (T-511b2a / ADR-0010) is a required keyword-only argument
    with no default — caller must specify ``"live"`` or ``"paper"`` based on
    the parent trade's ``BotConfig.exchange.mode``. Routes ``parent_trade_id``
    to either ``trades.id`` or ``paper_trades.id``.

    L-011 B-mode: ``meta`` (when non-None) serialized via
    ``json.dumps(_to_jsonable(meta))`` text-mode (no codec on execution-service).
    See module docstring for switch trigger if codec registers.
    """
    row = await conn.fetchrow(
        _INSERT_SHADOW_VARIANT_SQL,
        parent_trade_id,
        bot_id,
        variant_name,
        side,
        entry_price,
        qty,
        created_at,
        _serialize_meta(meta),
        parent_kind,
    )
    if row is None:
        msg = "INSERT shadow_variants ... RETURNING produced no row"
        raise RuntimeError(msg)
    return _row_to_shadow_variant(row)


@non_idempotent
async def insert_shadow_rejected(
    conn: _DbExecutor,
    *,
    signal_id: int,
    bot_id: str,
    symbol: str,
    would_side: str,
    created_at: datetime,
    meta: dict[str, Any] | None = None,
) -> ShadowRejectedRow:
    """INSERT shadow_rejected row; ``meta=None`` → SQL DEFAULT ``'{}'::jsonb`` applies."""
    row = await conn.fetchrow(
        _INSERT_SHADOW_REJECTED_SQL,
        signal_id,
        bot_id,
        symbol,
        would_side,
        created_at,
        _serialize_meta(meta),
    )
    if row is None:
        msg = "INSERT shadow_rejected ... RETURNING produced no row"
        raise RuntimeError(msg)
    return _row_to_shadow_rejected(row)


@non_idempotent
async def update_shadow_variant_terminal(
    conn: _DbExecutor,
    *,
    variant_id: int,
    terminated_at: datetime,
    terminal_outcome: ShadowVariantTerminal,
    realized_pnl: Decimal | None = None,
    mfe_pct: float | None = None,
    mae_pct: float | None = None,
) -> ShadowVariantRow | None:
    """UPDATE shadow_variants WHERE id=$1 SET terminal fields RETURNING.

    Returns None if row missing — defensive against cascade-delete race
    on parent_trade_id (caller logs warning + continues; no retry per
    @non_idempotent).
    """
    row = await conn.fetchrow(
        _UPDATE_VARIANT_TERMINAL_SQL,
        variant_id,
        terminated_at,
        terminal_outcome.value,
        realized_pnl,
        mfe_pct,
        mae_pct,
    )
    return _row_to_shadow_variant(row) if row is not None else None


@non_idempotent
async def update_shadow_rejected_terminal(
    conn: _DbExecutor,
    *,
    rejected_id: int,
    terminated_at: datetime,
    terminal_outcome: ShadowRejectedTerminal,
    mfe_pct: float | None = None,
    mae_pct: float | None = None,
) -> ShadowRejectedRow | None:
    """UPDATE shadow_rejected WHERE id=$1 SET terminal fields RETURNING.

    Returns None if row missing — defensive (no retry per @non_idempotent).
    No ``realized_pnl`` arg — rejected signals have no live trade.
    """
    row = await conn.fetchrow(
        _UPDATE_REJECTED_TERMINAL_SQL,
        rejected_id,
        terminated_at,
        terminal_outcome.value,
        mfe_pct,
        mae_pct,
    )
    return _row_to_shadow_rejected(row) if row is not None else None
