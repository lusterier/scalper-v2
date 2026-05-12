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
    "ShadowVariantAggregateRow",
    "ShadowVariantRow",
    "count_shadow_rejected",
    "insert_shadow_rejected",
    "insert_shadow_variant",
    "select_active_shadow_rejected",
    "select_active_shadow_variants",
    "select_all_active_shadow_rejected",
    "select_all_active_shadow_variants",
    "select_shadow_rejected_by_id",
    "select_shadow_rejected_paginated",
    "select_shadow_variant_by_id",
    "select_shadow_variants_by_parent",
    "select_shadow_variants_for_aggregate",
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


@dataclass(frozen=True, slots=True)
class ShadowVariantAggregateRow:
    """Subset projection used by per-symbol aggregate (T-517a1).

    8-field subset of :class:`ShadowVariantRow` plus parent_symbol via JOIN
    on ``trades`` (parent_kind='live') OR ``paper_trades`` (parent_kind='paper').
    Excludes meta/id/qty/side/entry_price/variant_name details that aggregate
    doesn't consume.

    Charter invariant (per analytics_compute pattern): row only present when
    ``terminated_at IS NOT NULL AND realized_pnl IS NOT NULL`` — only finalized
    P&L counts for stats. Excludes ``shutdown_mid_replay`` rows where realized_pnl
    is NULL.
    """

    parent_symbol: str
    bot_id: str
    variant_name: str
    realized_pnl: Decimal
    mfe_pct: float | None
    mae_pct: float | None
    parent_kind: Literal["live", "paper"]
    created_at: datetime


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

_SELECT_ALL_ACTIVE_REJECTED_SQL = (
    f"SELECT {_SHADOW_REJECTED_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM shadow_rejected"
    " WHERE terminated_at IS NULL"
    " ORDER BY created_at ASC"
)

_SELECT_VARIANT_BY_ID_SQL = (
    f"SELECT {_SHADOW_VARIANT_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM shadow_variants WHERE id = $1"
)

_SELECT_VARIANTS_BY_PARENT_SQL = (
    f"SELECT {_SHADOW_VARIANT_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM shadow_variants"
    " WHERE parent_trade_id = $1 AND parent_kind = $2"
    " ORDER BY variant_name ASC"
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


async def select_all_active_shadow_rejected(
    conn: _DbExecutor,
) -> list[ShadowRejectedRow]:
    """Cross-bot enumeration of active rejected observations ordered by created_at ASC (T-513b1).

    Used by ``services.execution.app.shadow_rejected_replay.resume_active_observations_on_startup``
    on lifespan startup to iterate every rejected-signal observation pending
    across all bots. Differs from :func:`select_active_shadow_rejected` — no
    ``bot_id`` filter; sequential index scan on ``shadow_rejected_bot_active``
    partial index (``terminated_at IS NULL`` filter) acceptable at expected
    F5 scale (<500 active observations total per H-016 + T-510a docstring).
    Mirror :func:`select_all_active_shadow_variants` (T-512a shipped).
    """
    rows = await conn.fetch(_SELECT_ALL_ACTIVE_REJECTED_SQL)
    return [_row_to_shadow_rejected(row) for row in rows]


async def select_shadow_variant_by_id(
    conn: _DbExecutor,
    *,
    variant_id: int,
) -> ShadowVariantRow | None:
    """Return ShadowVariantRow on hit; None on miss."""
    row = await conn.fetchrow(_SELECT_VARIANT_BY_ID_SQL, variant_id)
    return _row_to_shadow_variant(row) if row is not None else None


async def select_shadow_variants_by_parent(
    conn: _DbExecutor,
    *,
    parent_trade_id: int,
    parent_kind: Literal["live", "paper"],
) -> list[ShadowVariantRow]:
    """Return all variants (terminated + active) for ``parent_trade_id`` + ``parent_kind``.

    ORDER BY ``variant_name`` ASC for stable render order. Used by T-516b
    ``GET /api/trades/{id}/shadow-variants`` + ``GET /api/paper-trades/{id}/shadow-variants``
    drill-down endpoints. Empty list if no variants match the
    (parent_trade_id, parent_kind) tuple — caller treats empty as
    valid (no 404).

    ``parent_kind`` discriminator routes the query against the
    composite key per ADR-0010 — migration 0015 dropped the original
    0014 FK to ``trades.id`` so paper-mode parent IDs (``paper_trades.id``
    BIGSERIAL) coexist with live-mode parent IDs without collision
    constraints.
    """
    rows = await conn.fetch(_SELECT_VARIANTS_BY_PARENT_SQL, parent_trade_id, parent_kind)
    return [_row_to_shadow_variant(row) for row in rows]


async def select_shadow_rejected_by_id(
    conn: _DbExecutor,
    *,
    rejected_id: int,
) -> ShadowRejectedRow | None:
    """Return ShadowRejectedRow on hit; None on miss."""
    row = await conn.fetchrow(_SELECT_REJECTED_BY_ID_SQL, rejected_id)
    return _row_to_shadow_rejected(row) if row is not None else None


def _build_shadow_rejected_where_clause(
    *,
    bot_id: str | None,
    symbol: str | None,
    status: Literal["active", "terminated"] | None,
    terminal_outcome: ShadowRejectedTerminal | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Compose dynamic WHERE clause + bind args for shadow_rejected paginated/count.

    Mirror :func:`packages.db.queries.analytics._build_paper_trades_where_clause`.
    Returns ``("", [])`` when all filters are None. Otherwise returns
    ``("WHERE <predicates>", [<bind args in $N order>])`` using ``$N``
    placeholders ONLY (NEVER string interpolation per L-008 + §5.10).

    ``status`` filter encodes "active" → ``terminated_at IS NULL`` /
    "terminated" → ``terminated_at IS NOT NULL`` (constant predicate, no
    parameter site — does not consume a $N placeholder).

    ``from_at`` / ``to_at`` filter on ``created_at`` (rejected-observation
    start time; mirror :func:`select_signals_paginated` ``received_at``
    convention since rejected schema has no ``closed_at`` column).
    """
    predicates: list[str] = []
    bind_args: list[Any] = []
    if bot_id is not None:
        bind_args.append(bot_id)
        predicates.append(f"bot_id = ${len(bind_args)}")
    if symbol is not None:
        bind_args.append(symbol)
        predicates.append(f"symbol = ${len(bind_args)}")
    if status == "active":
        predicates.append("terminated_at IS NULL")
    elif status == "terminated":
        predicates.append("terminated_at IS NOT NULL")
    if terminal_outcome is not None:
        bind_args.append(terminal_outcome.value)
        predicates.append(f"terminal_outcome = ${len(bind_args)}")
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"created_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"created_at < ${len(bind_args)}")
    if not predicates:
        return ("", [])
    return ("WHERE " + " AND ".join(predicates), bind_args)


async def select_shadow_rejected_paginated(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    symbol: str | None,
    status: Literal["active", "terminated"] | None,
    terminal_outcome: ShadowRejectedTerminal | None,
    from_at: datetime | None,
    to_at: datetime | None,
    limit: int,
    offset: int,
) -> list[ShadowRejectedRow]:
    """Return one page of ``shadow_rejected`` rows with optional filters (T-517b1).

    ORDER BY ``created_at DESC, id DESC`` for "most recent first" per
    :func:`packages.db.queries.analytics.select_signals_paginated` precedent
    (rejected schema has no ``closed_at`` column; ``created_at`` is the
    natural time column and is non-nullable per migration 0014). limit/offset
    clamped by caller (router enforces 1 ≤ limit ≤ 200; 0 ≤ offset).
    """
    where_clause, where_args = _build_shadow_rejected_where_clause(
        bot_id=bot_id,
        symbol=symbol,
        status=status,
        terminal_outcome=terminal_outcome,
        from_at=from_at,
        to_at=to_at,
    )
    limit_placeholder = f"${len(where_args) + 1}"
    offset_placeholder = f"${len(where_args) + 2}"
    sql = (
        f"SELECT {_SHADOW_REJECTED_BASE_COLUMNS} FROM shadow_rejected "  # noqa: S608  # nosec B608
        f"{where_clause} "
        "ORDER BY created_at DESC, id DESC "
        f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
    )
    rows = await conn.fetch(sql, *where_args, limit, offset)
    return [_row_to_shadow_rejected(row) for row in rows]


async def count_shadow_rejected(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    symbol: str | None,
    status: Literal["active", "terminated"] | None,
    terminal_outcome: ShadowRejectedTerminal | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> int:
    """Return total ``shadow_rejected`` count matching the paginated filter set.

    Mirror :func:`packages.db.queries.analytics.count_paper_trades` — same
    :func:`_build_shadow_rejected_where_clause` helper so filter semantics
    stay in sync with :func:`select_shadow_rejected_paginated`.
    """
    where_clause, where_args = _build_shadow_rejected_where_clause(
        bot_id=bot_id,
        symbol=symbol,
        status=status,
        terminal_outcome=terminal_outcome,
        from_at=from_at,
        to_at=to_at,
    )
    sql = f"SELECT COUNT(*) AS n FROM shadow_rejected {where_clause}"  # noqa: S608  # nosec B608
    row = await conn.fetchrow(sql, *where_args)
    if row is None:
        return 0
    return int(row["n"])


# ---------------------------------------------------------------------------
# T-517a1 — per-symbol best-variant aggregate (BRIEF §13.6 second bullet)
# ---------------------------------------------------------------------------


_SELECT_AGGREGATE_SQL_PREFIX = (
    "SELECT COALESCE(t.symbol, pt.symbol) AS parent_symbol, v.bot_id, "
    "v.variant_name, v.realized_pnl, v.mfe_pct, v.mae_pct, v.parent_kind, "
    "v.created_at "
    "FROM shadow_variants v "
    "LEFT JOIN trades t ON v.parent_kind = 'live' AND v.parent_trade_id = t.id "
    "LEFT JOIN paper_trades pt ON v.parent_kind = 'paper' AND v.parent_trade_id = pt.id "
)
_SELECT_AGGREGATE_SQL_SUFFIX = " ORDER BY v.created_at DESC"


def _row_to_shadow_variant_aggregate(row: asyncpg.Record) -> ShadowVariantAggregateRow:
    """Narrow asyncpg row to typed :class:`ShadowVariantAggregateRow` (T-517a1).

    Mirror :func:`_row_to_shadow_variant` (15-col → 8-col subset). Decimal
    pass-through for ``realized_pnl`` (NUMERIC asyncpg native — no silent
    float cast per §5.3). DOUBLE PRECISION ``mfe_pct``/``mae_pct`` via
    ``float()`` with None-guard (rows where MFE/MAE were never recorded
    before observation closed). ``parent_kind`` literal-set guard mirrors
    :func:`_row_to_shadow_variant:249-252`.
    """
    parent_kind_raw = str(row["parent_kind"])
    if parent_kind_raw not in ("live", "paper"):
        msg = f"shadow_variants.parent_kind unexpected value {parent_kind_raw!r}"
        raise ValueError(msg)
    return ShadowVariantAggregateRow(
        parent_symbol=str(row["parent_symbol"]),
        bot_id=str(row["bot_id"]),
        variant_name=str(row["variant_name"]),
        realized_pnl=row["realized_pnl"],
        mfe_pct=float(row["mfe_pct"]) if row["mfe_pct"] is not None else None,
        mae_pct=float(row["mae_pct"]) if row["mae_pct"] is not None else None,
        parent_kind=parent_kind_raw,  # type: ignore[arg-type]
        created_at=row["created_at"],
    )


def _build_shadow_variant_aggregate_where_clause(
    *,
    symbol: str,
    bot_id: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Compose dynamic WHERE clause for shadow-variant per-symbol aggregate (T-517a1).

    Mirror :func:`_build_shadow_rejected_where_clause` sibling pattern
    modulo always-included charter predicates. ``$N`` placeholders ONLY
    (NEVER string interpolation per L-008 + §5.10).

    Charter predicates ALWAYS included (per analytics charter "only finalized
    P&L counts for stats" at analytics.py:1735):

    * ``COALESCE(t.symbol, pt.symbol) = $1::text`` — ``$1::text`` defensive
      cast (COALESCE result is TEXT; explicit cast is safer per L-021).
    * ``v.terminated_at IS NOT NULL``
    * ``v.realized_pnl IS NOT NULL``

    Optional filters appended as direct column comparisons (no L-021 cast
    needed — PG infers from column type; eliminates ``$N::type IS NULL OR ...``
    pattern entirely):

    * ``bot_id`` → ``v.bot_id = $N``
    * ``from_at`` → ``v.created_at >= $N``
    * ``to_at`` → ``v.created_at < $N``

    Returns ``("WHERE <predicates>", [bind args in $N order])``. ``symbol``
    is always ``$1``; optional filters take ``$2..$4`` as appended.
    """
    bind_args: list[Any] = [symbol]
    predicates: list[str] = [
        "COALESCE(t.symbol, pt.symbol) = $1::text",
        "v.terminated_at IS NOT NULL",
        "v.realized_pnl IS NOT NULL",
    ]
    if bot_id is not None:
        bind_args.append(bot_id)
        predicates.append(f"v.bot_id = ${len(bind_args)}")
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"v.created_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"v.created_at < ${len(bind_args)}")
    return ("WHERE " + " AND ".join(predicates), bind_args)


async def select_shadow_variants_for_aggregate(
    conn: _DbExecutor,
    *,
    symbol: str,
    bot_id: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> list[ShadowVariantAggregateRow]:
    """Return terminated variants for ``symbol`` with optional bot + window filters (T-517a1).

    JOINs ``shadow_variants`` with parent ``trades`` (parent_kind='live') OR
    ``paper_trades`` (parent_kind='paper') by ``parent_trade_id`` to pull
    parent symbol via COALESCE. Charter invariant: only ``terminated_at IS
    NOT NULL AND realized_pnl IS NOT NULL`` rows (mirror
    :func:`packages.db.queries.analytics.select_trades_for_analytics` charter
    at analytics.py:1735 — "only finalized P&L counts for stats").

    ORDER BY ``v.created_at DESC`` for deterministic iteration; in-memory
    aggregator is order-invariant for total/avg/min/max metrics.
    """
    where_clause, where_args = _build_shadow_variant_aggregate_where_clause(
        symbol=symbol,
        bot_id=bot_id,
        from_at=from_at,
        to_at=to_at,
    )
    sql = f"{_SELECT_AGGREGATE_SQL_PREFIX}{where_clause}{_SELECT_AGGREGATE_SQL_SUFFIX}"
    rows = await conn.fetch(sql, *where_args)
    return [_row_to_shadow_variant_aggregate(row) for row in rows]


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
