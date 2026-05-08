"""analytics-api query module (§7.2, §9.6).

Owned by ``packages/db/queries``; consumed by analytics-api router
endpoints to read ``bots`` rows + read/write ``symbol_map`` rows + read
``position_state`` + ``trades``. Raw asyncpg per brief §5.10 ("all
queries in hot paths are raw SQL via asyncpg, parameterized").

T-401a half: ``select_all_bots`` + ``select_bot_by_id`` + ``BotDetailRow``.
T-401b extends: ``SymbolMapRow`` + 5 symbol_map functions
(``select_all_symbol_map_entries`` + ``select_symbol_map_entry`` +
``insert_symbol_map_entry`` + ``update_symbol_map_entry`` +
``delete_symbol_map_entry``). Write helpers ``@non_idempotent`` per §N3.
T-402 extends: ``OpenPositionRow`` + ``TradeRow`` + 4 read functions
(``select_open_positions`` + ``select_trades_paginated`` +
``count_trades`` + ``select_trade_by_id``) for `/api/positions/*` +
`/api/trades/*` dashboard endpoints. Dynamic SQL filter builder
``_build_trades_where_clause`` constructs WHERE clause via ``$N``
placeholders only (NEVER string interpolation per L-008 + §5.10).
T-403 extends: ``SignalRow`` + ``ScoringEvaluationRow`` + 4 read
functions (``select_signals_paginated`` + ``count_signals`` +
``select_signal_by_id`` + ``select_scoring_evaluations_by_signal_id``)
for `/api/signals/*` + `/api/scoring/*` dashboard endpoints. Mirror
T-402 dynamic builder pattern via ``_build_signals_where_clause``
(6 filters; `$N` placeholders only).
T-404 extends: ``FeatureRow`` + 4 read functions
(``select_latest_features`` + ``count_latest_features`` +
``select_features_history`` + ``count_features_history``) for
`/api/features/*` dashboard endpoints. DISTINCT ON via
``features_latest (feature_name, symbol, computed_at DESC)`` index
per §7.2:914. Mirror T-403 dynamic builder pattern via
``_build_features_history_where_clause`` (`$N` placeholders only).
``FeatureRow`` is full 7-field projection for analytics-api endpoints;
distinct from ``feature_engine.LatestFeatureRow`` 4-field flat
projection that resolver consumes for scoring (different ownership
per T-401a precedent).
T-405 extends: ``BotConfigRow`` + 7 functions
(``select_bot_config_current`` + ``select_bot_config_versions`` +
``count_bot_config_versions`` + ``select_bot_config_by_version`` +
``select_max_bot_config_version`` + ``insert_bot_config`` +
``update_bot_config_applied``) for `/api/configs/*` endpoints, plus
3 audit-reader functions (``select_audit_events_paginated`` +
``count_audit_events`` + ``select_audit_event_by_id``) for
`/api/audit/*`. Audit reader uses :class:`packages.db.queries.audit.AuditEventRow`
(T-401a). Dynamic helper ``_build_audit_where_clause`` mirror
T-402/T-403/T-404 pattern.

T-407 extends: ``BacktestRunRow`` + 4 functions
(``select_backtest_runs_paginated`` + ``count_backtest_runs`` +
``select_backtest_run_by_id`` + ``insert_backtest_run``) for
`/api/backtests/*` endpoints. `_build_backtests_where_clause` mirror
T-402..T-405 dynamic builder pattern (4 filters; `$N` placeholders only
per L-008). UUID PK from ``gen_random_uuid()`` (migration 0012 enables
``pgcrypto`` extension).

``BotStatus`` / ``ExchangeMode`` / ``ExchangeSource`` / ``TradeStatus``
/ ``BacktestStatus`` enum narrowing uses canonical
:mod:`packages.core.types` StrEnums; the StrEnum constructor itself
raises :class:`ValueError` on unknown values, so no hand-rolled
validator is needed (cleaner than promoting the private
``_validate_exchange_mode`` from :mod:`packages.db.queries.execution`
per T-401a WG#2 plan-reviewer alternative + T-401b WG#1 + T-402 WG#1
consistency).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from packages.core import idempotent, non_idempotent
from packages.core.types import (
    Action,
    BacktestStatus,
    BotStatus,
    ExchangeMode,
    ExchangeSource,
    IngestionStatus,
    ScoringDecision,
    TradeStatus,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = [
    "BacktestRunRow",
    "BacktestTradeRow",
    "BotConfigRow",
    "BotDetailRow",
    "FeatureRow",
    "OpenPositionRow",
    "PaperTradeRow",
    "ScoringEvaluationRow",
    "SignalRow",
    "SymbolMapRow",
    "TradeRealizedPnlRow",
    "TradeRow",
    "count_audit_events",
    "count_backtest_runs",
    "count_bot_config_versions",
    "count_features_history",
    "count_latest_features",
    "count_paper_trades",
    "count_signals",
    "count_trades",
    "delete_symbol_map_entry",
    "insert_backtest_run",
    "insert_bot_config",
    "insert_symbol_map_entry",
    "select_all_bots",
    "select_all_symbol_map_entries",
    "select_audit_event_by_id",
    "select_audit_events_paginated",
    "select_backtest_run_by_id",
    "select_backtest_runs_paginated",
    "select_bot_by_id",
    "select_bot_config_by_version",
    "select_bot_config_current",
    "select_bot_config_versions",
    "select_features_history",
    "select_latest_features",
    "select_max_bot_config_version",
    "select_open_positions",
    "select_paper_trade_by_id",
    "select_paper_trades_paginated",
    "select_scoring_evaluations_by_signal_id",
    "select_signal_by_id",
    "select_signals_paginated",
    "select_symbol_map_entry",
    "select_trade_by_id",
    "select_trades_by_run",
    "select_trades_for_analytics",  # T-406
    "select_trades_paginated",
    "update_bot_config_applied",
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


# ---------------------------------------------------------------------------
# T-402 — /api/positions/* + /api/trades/* read endpoints (§7.2:983-1080, §9.6:1623-1624)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenPositionRow:
    """Full projection of ``position_state`` row (16 fields per §7.2:1058-1080).

    Distinct from :class:`packages.db.queries.execution.PositionStateRow`
    — that is a 14-field projection used by execution-service post-restart
    reconciliation (T-221), without ``tp_hit`` / ``trailing_active`` /
    ``updated_at``. Analytics-api needs the full row for the dashboard
    Per-bot live view section (BRIEF §14.3:2061).
    """

    bot_id: str
    symbol: str
    trade_id: int
    side: str
    entry_price: Decimal
    qty: Decimal
    remaining_qty: Decimal
    sl_price: Decimal | None
    tp_price: Decimal | None
    sl_type: str | None
    best_price: Decimal | None
    tp_hit: bool
    trailing_active: bool
    running_pnl: Decimal
    mfe_price: Decimal | None
    mae_price: Decimal | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TradeRow:
    """Full projection of ``trades`` row (19 fields per §7.2:983-1011).

    DOUBLE PRECISION fields (``mfe_pct`` / ``mae_pct`` / ``confidence_score``)
    stay as ``float`` per §5.13 — statistical ratios, not money. NUMERIC
    fields stay as ``Decimal`` per §N1 / §5.3 precision invariant.
    """

    id: int
    bot_id: str
    signal_id: int | None
    open_order_id: int
    close_order_id: int | None
    symbol: str
    side: str
    entry_price: Decimal
    exit_price: Decimal | None
    qty: Decimal
    notional_usd: Decimal
    realized_pnl: Decimal | None
    fees_paid: Decimal | None
    close_reason: str | None
    opened_at: datetime
    closed_at: datetime | None
    status: TradeStatus
    mfe_pct: float | None
    mae_pct: float | None
    confidence_score: float | None
    meta: dict[str, Any]


_SELECT_OPEN_POSITIONS_ALL_SQL = """
    SELECT bot_id, symbol, trade_id, side, entry_price, qty, remaining_qty,
           sl_price, tp_price, sl_type, best_price, tp_hit, trailing_active,
           running_pnl, mfe_price, mae_price, updated_at
    FROM position_state
    ORDER BY bot_id, symbol
"""

_SELECT_OPEN_POSITIONS_BY_BOT_SQL = """
    SELECT bot_id, symbol, trade_id, side, entry_price, qty, remaining_qty,
           sl_price, tp_price, sl_type, best_price, tp_hit, trailing_active,
           running_pnl, mfe_price, mae_price, updated_at
    FROM position_state
    WHERE bot_id = $1
    ORDER BY symbol
"""

_TRADES_BASE_COLUMNS = (
    "id, bot_id, signal_id, open_order_id, close_order_id, symbol, side, "
    "entry_price, exit_price, qty, notional_usd, realized_pnl, fees_paid, "
    "close_reason, opened_at, closed_at, status, mfe_pct, mae_pct, "
    "confidence_score, meta"
)

_SELECT_TRADE_BY_ID_SQL = f"SELECT {_TRADES_BASE_COLUMNS} FROM trades WHERE id = $1"  # noqa: S608 — column whitelist constant, no user input  # nosec B608


def _row_to_open_position(row: asyncpg.Record) -> OpenPositionRow:
    return OpenPositionRow(
        bot_id=str(row["bot_id"]),
        symbol=str(row["symbol"]),
        trade_id=int(row["trade_id"]),
        side=str(row["side"]),
        entry_price=row["entry_price"],
        qty=row["qty"],
        remaining_qty=row["remaining_qty"],
        sl_price=row["sl_price"],
        tp_price=row["tp_price"],
        sl_type=str(row["sl_type"]) if row["sl_type"] is not None else None,
        best_price=row["best_price"],
        tp_hit=bool(row["tp_hit"]),
        trailing_active=bool(row["trailing_active"]),
        running_pnl=row["running_pnl"],
        mfe_price=row["mfe_price"],
        mae_price=row["mae_price"],
        updated_at=row["updated_at"],
    )


def _row_to_trade(row: asyncpg.Record) -> TradeRow:
    meta_value = row["meta"]
    return TradeRow(
        id=int(row["id"]),
        bot_id=str(row["bot_id"]),
        signal_id=int(row["signal_id"]) if row["signal_id"] is not None else None,
        open_order_id=int(row["open_order_id"]),
        close_order_id=(int(row["close_order_id"]) if row["close_order_id"] is not None else None),
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        qty=row["qty"],
        notional_usd=row["notional_usd"],
        realized_pnl=row["realized_pnl"],
        fees_paid=row["fees_paid"],
        close_reason=(str(row["close_reason"]) if row["close_reason"] is not None else None),
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        status=TradeStatus(str(row["status"])),
        mfe_pct=float(row["mfe_pct"]) if row["mfe_pct"] is not None else None,
        mae_pct=float(row["mae_pct"]) if row["mae_pct"] is not None else None,
        confidence_score=(
            float(row["confidence_score"]) if row["confidence_score"] is not None else None
        ),
        meta=meta_value if isinstance(meta_value, dict) else {},
    )


async def select_open_positions(
    conn: _DbExecutor,
    *,
    bot_id: str | None = None,
) -> list[OpenPositionRow]:
    """Return all rows from ``position_state``, optionally filtered by ``bot_id``.

    ``position_state`` only contains OPEN positions by definition (T-219
    deletes on close); no status filter needed. ORDER BY ``bot_id, symbol``
    when unfiltered, ``symbol`` only when filtered to one bot — both cases
    deterministic for UI rendering.
    """
    if bot_id is None:
        rows = await conn.fetch(_SELECT_OPEN_POSITIONS_ALL_SQL)
    else:
        rows = await conn.fetch(_SELECT_OPEN_POSITIONS_BY_BOT_SQL, bot_id)
    return [_row_to_open_position(row) for row in rows]


def _build_trades_where_clause(
    *,
    bot_id: str | None,
    symbol: str | None,
    status: TradeStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Compose dynamic WHERE clause + bind args for ``select_trades_paginated`` + ``count_trades``.

    Returns ``("", [])`` when all filters are None (no WHERE clause).
    Otherwise returns ``("WHERE <predicates>", [<bind args in $N order>])``
    using ``$N`` placeholders ONLY (NEVER string interpolation per L-008
    + §5.10). Filter slots are AND-combined.

    ``from_at`` / ``to_at`` filter on ``closed_at`` only (open trades have
    ``closed_at IS NULL`` and are excluded by ``closed_at >= $N``); use
    ``status='open'`` filter separately for open trades. WG#6 simplification
    — single-column range filter; no conditional column based on status.
    """
    predicates: list[str] = []
    bind_args: list[Any] = []
    if bot_id is not None:
        bind_args.append(bot_id)
        predicates.append(f"bot_id = ${len(bind_args)}")
    if symbol is not None:
        bind_args.append(symbol)
        predicates.append(f"symbol = ${len(bind_args)}")
    if status is not None:
        bind_args.append(str(status))
        predicates.append(f"status = ${len(bind_args)}")
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"closed_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"closed_at < ${len(bind_args)}")
    if not predicates:
        return ("", [])
    return ("WHERE " + " AND ".join(predicates), bind_args)


async def select_trades_paginated(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    symbol: str | None,
    status: TradeStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
    limit: int,
    offset: int,
) -> list[TradeRow]:
    """Return one page of trades with optional filters.

    ORDER BY ``closed_at DESC NULLS FIRST`` so most-recent closed trades
    sort first with open trades floating to top. Limit/offset clamped by
    caller (router enforces 1 ≤ limit ≤ 200; 0 ≤ offset).
    """
    where_clause, where_args = _build_trades_where_clause(
        bot_id=bot_id,
        symbol=symbol,
        status=status,
        from_at=from_at,
        to_at=to_at,
    )
    limit_placeholder = f"${len(where_args) + 1}"
    offset_placeholder = f"${len(where_args) + 2}"
    # _TRADES_BASE_COLUMNS + where_clause + placeholder strings are all
    # derived from compile-time constants + $N integers; no user input
    # ever reaches the SQL string (filter values bind via where_args).
    sql = (
        f"SELECT {_TRADES_BASE_COLUMNS} FROM trades "  # noqa: S608  # nosec B608
        f"{where_clause} "
        "ORDER BY closed_at DESC NULLS FIRST, id DESC "
        f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
    )
    rows = await conn.fetch(sql, *where_args, limit, offset)
    return [_row_to_trade(row) for row in rows]


async def count_trades(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    symbol: str | None,
    status: TradeStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> int:
    """Return total count of trades matching the same filters as :func:`select_trades_paginated`.

    Uses the same :func:`_build_trades_where_clause` helper so filter
    semantics stay in sync (no drift between count and page query).
    """
    where_clause, where_args = _build_trades_where_clause(
        bot_id=bot_id,
        symbol=symbol,
        status=status,
        from_at=from_at,
        to_at=to_at,
    )
    sql = f"SELECT COUNT(*) AS n FROM trades {where_clause}"  # noqa: S608 — where_clause is parameterized via $N  # nosec B608
    row = await conn.fetchrow(sql, *where_args)
    if row is None:
        return 0
    return int(row["n"])


async def select_trade_by_id(
    conn: _DbExecutor,
    trade_id: int,
) -> TradeRow | None:
    """Return one ``trades`` row by PK; ``None`` if not found."""
    row = await conn.fetchrow(_SELECT_TRADE_BY_ID_SQL, trade_id)
    return _row_to_trade(row) if row is not None else None


@dataclass(frozen=True, slots=True)
class PaperTradeRow:
    """Full projection of ``paper_trades`` row (21 fields per §12.1 paper_trades + migration 0008).

    Structurally identical to :class:`TradeRow` (same 21 columns; same types
    + nullability) — paper_trades schema mirrors trades schema 1:1 per
    §3.1:268 paper-live symmetry invariant. T-516a1 mirrors live read-side
    surface for paper analytics drill-down.

    DOUBLE PRECISION fields (``mfe_pct`` / ``mae_pct`` / ``confidence_score``)
    stay as ``float`` per §5.13 — statistical ratios, not money. NUMERIC
    fields stay as ``Decimal`` per §N1 / §5.3 precision invariant.
    """

    id: int
    bot_id: str
    signal_id: int | None
    open_order_id: int
    close_order_id: int | None
    symbol: str
    side: str
    entry_price: Decimal
    exit_price: Decimal | None
    qty: Decimal
    notional_usd: Decimal
    realized_pnl: Decimal | None
    fees_paid: Decimal | None
    close_reason: str | None
    opened_at: datetime
    closed_at: datetime | None
    status: TradeStatus
    mfe_pct: float | None
    mae_pct: float | None
    confidence_score: float | None
    meta: dict[str, Any]


_PAPER_TRADES_BASE_COLUMNS = (
    "id, bot_id, signal_id, open_order_id, close_order_id, symbol, side, "
    "entry_price, exit_price, qty, notional_usd, realized_pnl, fees_paid, "
    "close_reason, opened_at, closed_at, status, mfe_pct, mae_pct, "
    "confidence_score, meta"
)

_SELECT_PAPER_TRADE_BY_ID_SQL = (
    f"SELECT {_PAPER_TRADES_BASE_COLUMNS} FROM paper_trades WHERE id = $1"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
)


def _row_to_paper_trade(row: asyncpg.Record) -> PaperTradeRow:
    """Mirror :func:`_row_to_trade` byte-for-byte modulo dataclass name (per WG#3)."""
    meta_value = row["meta"]
    return PaperTradeRow(
        id=int(row["id"]),
        bot_id=str(row["bot_id"]),
        signal_id=int(row["signal_id"]) if row["signal_id"] is not None else None,
        open_order_id=int(row["open_order_id"]),
        close_order_id=(int(row["close_order_id"]) if row["close_order_id"] is not None else None),
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        qty=row["qty"],
        notional_usd=row["notional_usd"],
        realized_pnl=row["realized_pnl"],
        fees_paid=row["fees_paid"],
        close_reason=(str(row["close_reason"]) if row["close_reason"] is not None else None),
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        status=TradeStatus(str(row["status"])),
        mfe_pct=float(row["mfe_pct"]) if row["mfe_pct"] is not None else None,
        mae_pct=float(row["mae_pct"]) if row["mae_pct"] is not None else None,
        confidence_score=(
            float(row["confidence_score"]) if row["confidence_score"] is not None else None
        ),
        meta=meta_value if isinstance(meta_value, dict) else {},
    )


def _build_paper_trades_where_clause(
    *,
    bot_id: str | None,
    symbol: str | None,
    status: TradeStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Mirror :func:`_build_trades_where_clause` for ``paper_trades`` (per WG#4).

    Returns ``("", [])`` when all filters are None (no WHERE clause). Otherwise
    returns ``("WHERE <predicates>", [<bind args in $N order>])`` using ``$N``
    placeholders ONLY (NEVER string interpolation per L-008 + §5.10). Filter
    slots are AND-combined. ``from_at`` / ``to_at`` filter on ``closed_at`` only.
    """
    predicates: list[str] = []
    bind_args: list[Any] = []
    if bot_id is not None:
        bind_args.append(bot_id)
        predicates.append(f"bot_id = ${len(bind_args)}")
    if symbol is not None:
        bind_args.append(symbol)
        predicates.append(f"symbol = ${len(bind_args)}")
    if status is not None:
        bind_args.append(str(status))
        predicates.append(f"status = ${len(bind_args)}")
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"closed_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"closed_at < ${len(bind_args)}")
    if not predicates:
        return ("", [])
    return ("WHERE " + " AND ".join(predicates), bind_args)


async def select_paper_trades_paginated(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    symbol: str | None,
    status: TradeStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
    limit: int,
    offset: int,
) -> list[PaperTradeRow]:
    """Return one page of paper_trades with optional filters.

    Mirror :func:`select_trades_paginated` 1:1 modulo target table. ORDER BY
    ``closed_at DESC NULLS FIRST, id DESC`` per WG#5; limit/offset clamped by
    caller (router enforces 1 ≤ limit ≤ 200; 0 ≤ offset).
    """
    where_clause, where_args = _build_paper_trades_where_clause(
        bot_id=bot_id,
        symbol=symbol,
        status=status,
        from_at=from_at,
        to_at=to_at,
    )
    limit_placeholder = f"${len(where_args) + 1}"
    offset_placeholder = f"${len(where_args) + 2}"
    sql = (
        f"SELECT {_PAPER_TRADES_BASE_COLUMNS} FROM paper_trades "  # noqa: S608  # nosec B608
        f"{where_clause} "
        "ORDER BY closed_at DESC NULLS FIRST, id DESC "
        f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
    )
    rows = await conn.fetch(sql, *where_args, limit, offset)
    return [_row_to_paper_trade(row) for row in rows]


async def count_paper_trades(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    symbol: str | None,
    status: TradeStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> int:
    """Return total paper_trades count matching :func:`select_paper_trades_paginated` filters.

    Mirror :func:`count_trades` — same :func:`_build_paper_trades_where_clause`
    helper so filter semantics stay in sync.
    """
    where_clause, where_args = _build_paper_trades_where_clause(
        bot_id=bot_id,
        symbol=symbol,
        status=status,
        from_at=from_at,
        to_at=to_at,
    )
    sql = f"SELECT COUNT(*) AS n FROM paper_trades {where_clause}"  # noqa: S608 — where_clause parameterized via $N  # nosec B608
    row = await conn.fetchrow(sql, *where_args)
    if row is None:
        return 0
    return int(row["n"])


async def select_paper_trade_by_id(
    conn: _DbExecutor,
    paper_trade_id: int,
) -> PaperTradeRow | None:
    """Return one ``paper_trades`` row by PK; ``None`` if not found."""
    row = await conn.fetchrow(_SELECT_PAPER_TRADE_BY_ID_SQL, paper_trade_id)
    return _row_to_paper_trade(row) if row is not None else None


# ---------------------------------------------------------------------------
# T-403 — /api/signals/* + /api/scoring/* read endpoints (§7.2:880-1055, §9.6:1625-1626)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalRow:
    """Full projection of ``signals`` row (12 fields per §7.2:880-893)."""

    id: int
    received_at: datetime
    schema_version: str
    source: str
    idempotency_key: str
    symbol: str
    original_symbol: str | None
    action: Action
    payload: dict[str, Any]
    ingestion_status: IngestionStatus
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ScoringEvaluationRow:
    """Full projection of ``scoring_evaluations`` row (11 fields per §7.2:1039-1051).

    DOUBLE PRECISION fields (``trigger_threshold`` / ``total_score``) stay
    as ``float`` per §5.13 — statistical metrics, not money.
    """

    id: int
    bot_id: str
    signal_id: int
    evaluated_at: datetime
    trigger_threshold: float
    total_score: float
    decision: ScoringDecision
    config_version: int
    rule_results: list[dict[str, Any]]
    feature_snapshot: dict[str, Any]
    correlation_id: str


_SIGNALS_BASE_COLUMNS = (
    "id, received_at, schema_version, source, idempotency_key, symbol, "
    "original_symbol, action, payload, ingestion_status, correlation_id"
)

_SELECT_SIGNAL_BY_ID_SQL = f"SELECT {_SIGNALS_BASE_COLUMNS} FROM signals WHERE id = $1"  # noqa: S608  # nosec B608

_SELECT_SCORING_BY_SIGNAL_ID_SQL = """
    SELECT id, bot_id, signal_id, evaluated_at, trigger_threshold,
           total_score, decision, config_version, rule_results,
           feature_snapshot, correlation_id
    FROM scoring_evaluations
    WHERE signal_id = $1
    ORDER BY bot_id ASC
"""


def _row_to_signal(row: asyncpg.Record) -> SignalRow:
    payload = row["payload"]
    return SignalRow(
        id=int(row["id"]),
        received_at=row["received_at"],
        schema_version=str(row["schema_version"]),
        source=str(row["source"]),
        idempotency_key=str(row["idempotency_key"]),
        symbol=str(row["symbol"]),
        original_symbol=(
            str(row["original_symbol"]) if row["original_symbol"] is not None else None
        ),
        action=Action(str(row["action"])),
        payload=payload if isinstance(payload, dict) else {},
        ingestion_status=IngestionStatus(str(row["ingestion_status"])),
        correlation_id=str(row["correlation_id"]),
    )


def _row_to_scoring_evaluation(row: asyncpg.Record) -> ScoringEvaluationRow:
    rule_results = row["rule_results"]
    feature_snapshot = row["feature_snapshot"]
    return ScoringEvaluationRow(
        id=int(row["id"]),
        bot_id=str(row["bot_id"]),
        signal_id=int(row["signal_id"]),
        evaluated_at=row["evaluated_at"],
        trigger_threshold=float(row["trigger_threshold"]),
        total_score=float(row["total_score"]),
        decision=ScoringDecision(str(row["decision"])),
        config_version=int(row["config_version"]),
        rule_results=rule_results if isinstance(rule_results, list) else [],
        feature_snapshot=feature_snapshot if isinstance(feature_snapshot, dict) else {},
        correlation_id=str(row["correlation_id"]),
    )


def _build_signals_where_clause(
    *,
    source: str | None,
    symbol: str | None,
    action: Action | None,
    ingestion_status: IngestionStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Compose dynamic WHERE clause + bind args for signals queries.

    Mirror :func:`_build_trades_where_clause` shape — `$N` placeholders
    only (never string interpolation per L-008 + §5.10). 6 filter slots
    (no `bot_id` — signals lack bot_id column). `from_at`/`to_at`
    filter on `received_at` column (NOT `closed_at` like trades).

    Returns ``("", [])`` when all filters are None (no WHERE clause).
    """
    predicates: list[str] = []
    bind_args: list[Any] = []
    if source is not None:
        bind_args.append(source)
        predicates.append(f"source = ${len(bind_args)}")
    if symbol is not None:
        bind_args.append(symbol)
        predicates.append(f"symbol = ${len(bind_args)}")
    if action is not None:
        bind_args.append(str(action))
        predicates.append(f"action = ${len(bind_args)}")
    if ingestion_status is not None:
        bind_args.append(str(ingestion_status))
        predicates.append(f"ingestion_status = ${len(bind_args)}")
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"received_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"received_at < ${len(bind_args)}")
    if not predicates:
        return ("", [])
    return ("WHERE " + " AND ".join(predicates), bind_args)


async def select_signals_paginated(
    conn: _DbExecutor,
    *,
    source: str | None,
    symbol: str | None,
    action: Action | None,
    ingestion_status: IngestionStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
    limit: int,
    offset: int,
) -> list[SignalRow]:
    """Return one page of signals with optional filters.

    ORDER BY ``received_at DESC, id DESC`` for "most recent first"
    deterministic pagination (signals.received_at is NOT NULL so no
    NULLS FIRST needed). `received_at >= from_at AND received_at < to_at`
    range filter (NOT closed_at like trades — signals have no close
    semantics).
    """
    where_clause, where_args = _build_signals_where_clause(
        source=source,
        symbol=symbol,
        action=action,
        ingestion_status=ingestion_status,
        from_at=from_at,
        to_at=to_at,
    )
    limit_placeholder = f"${len(where_args) + 1}"
    offset_placeholder = f"${len(where_args) + 2}"
    sql = (
        f"SELECT {_SIGNALS_BASE_COLUMNS} FROM signals "  # noqa: S608  # nosec B608
        f"{where_clause} "
        "ORDER BY received_at DESC, id DESC "
        f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
    )
    rows = await conn.fetch(sql, *where_args, limit, offset)
    return [_row_to_signal(row) for row in rows]


async def count_signals(
    conn: _DbExecutor,
    *,
    source: str | None,
    symbol: str | None,
    action: Action | None,
    ingestion_status: IngestionStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> int:
    """Return total count of signals matching same filters as :func:`select_signals_paginated`.

    Routes through :func:`_build_signals_where_clause` (no drift between
    count and page query).
    """
    where_clause, where_args = _build_signals_where_clause(
        source=source,
        symbol=symbol,
        action=action,
        ingestion_status=ingestion_status,
        from_at=from_at,
        to_at=to_at,
    )
    sql = f"SELECT COUNT(*) AS n FROM signals {where_clause}"  # noqa: S608  # nosec B608
    row = await conn.fetchrow(sql, *where_args)
    if row is None:
        return 0
    return int(row["n"])


async def select_signal_by_id(
    conn: _DbExecutor,
    signal_id: int,
) -> SignalRow | None:
    """Return one signals row by id; ``None`` if missing.

    NOTE: Hypertable PK is composite ``(received_at, id)`` — lookup by
    id alone has no chunk pruning predicate, so it walks every chunk.
    At MVP scale (10k-100k signals/month, 7-day chunks) acceptable;
    F5+ may add a ``signals_id`` btree index if perf bottleneck surfaces.
    """
    row = await conn.fetchrow(_SELECT_SIGNAL_BY_ID_SQL, signal_id)
    return _row_to_signal(row) if row is not None else None


async def select_scoring_evaluations_by_signal_id(
    conn: _DbExecutor,
    signal_id: int,
) -> list[ScoringEvaluationRow]:
    """Return all scoring evaluations for one ``signal_id`` (one per bot).

    NOTE: ``se_bot_signal (bot_id, signal_id)`` index per §7.2:1054 has
    ``bot_id`` as leading column — PostgreSQL CANNOT efficiently use
    this index for ``WHERE signal_id = $1`` (signal_id is non-leading).
    This query walks every chunk of the hypertable, same as
    :func:`select_signal_by_id`. At MVP scale acceptable; F5+ may add a
    separate ``se_signal (signal_id)`` index if perf bottleneck surfaces.
    ORDER BY ``bot_id ASC`` for deterministic UI ordering.
    """
    rows = await conn.fetch(_SELECT_SCORING_BY_SIGNAL_ID_SQL, signal_id)
    return [_row_to_scoring_evaluation(row) for row in rows]


# ---------------------------------------------------------------------------
# T-404 — /api/features/* read endpoints (§7.2:900-915, §9.6:1627)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """Full projection of ``features`` row (7 fields per §7.2:903-911).

    DOUBLE PRECISION ``value_num`` → float per §5.13 (statistical, not
    money). ``value_json`` JSONB column round-trips as dict OR list via
    T-401a's :func:`services.analytics_api.app.main._register_jsonb_codec`
    — Pydantic v2 union ``dict | list | None`` preserves shape.

    Distinct from :class:`packages.db.queries.feature_engine.LatestFeatureRow`
    4-field flat projection that the resolver consumes for scoring.
    Analytics-api needs the full 7-field projection for the Feature
    inspector UI (BRIEF §14.3:2065).
    """

    feature_name: str
    symbol: str
    computed_at: datetime
    value_num: float | None
    value_bool: bool | None
    value_json: dict[str, Any] | list[Any] | None
    source_version: str


_FEATURES_BASE_COLUMNS = (
    "feature_name, symbol, computed_at, value_num, value_bool, value_json, source_version"
)

_LATEST_FEATURES_NO_FILTER_SQL = """
    SELECT DISTINCT ON (feature_name, symbol)
           feature_name, symbol, computed_at, value_num, value_bool,
           value_json, source_version
    FROM features
    ORDER BY feature_name, symbol, computed_at DESC
    LIMIT $1 OFFSET $2
"""

_LATEST_FEATURES_WITH_PREFIX_SQL = """
    SELECT DISTINCT ON (feature_name, symbol)
           feature_name, symbol, computed_at, value_num, value_bool,
           value_json, source_version
    FROM features
    WHERE feature_name LIKE $1
    ORDER BY feature_name, symbol, computed_at DESC
    LIMIT $2 OFFSET $3
"""

_COUNT_LATEST_FEATURES_NO_FILTER_SQL = """
    SELECT COUNT(*) AS n FROM (
        SELECT DISTINCT feature_name, symbol FROM features
    ) AS t
"""

_COUNT_LATEST_FEATURES_WITH_PREFIX_SQL = """
    SELECT COUNT(*) AS n FROM (
        SELECT DISTINCT feature_name, symbol FROM features WHERE feature_name LIKE $1
    ) AS t
"""


def _row_to_feature(row: asyncpg.Record) -> FeatureRow:
    value_json = row["value_json"]
    if value_json is not None and not isinstance(value_json, (dict, list)):
        # Defensive: JSONB codec absent → fall back to None so Pydantic
        # union doesn't choke on raw string.
        value_json = None
    return FeatureRow(
        feature_name=str(row["feature_name"]),
        symbol=str(row["symbol"]),
        computed_at=row["computed_at"],
        value_num=(float(row["value_num"]) if row["value_num"] is not None else None),
        value_bool=(bool(row["value_bool"]) if row["value_bool"] is not None else None),
        value_json=value_json,
        source_version=str(row["source_version"]),
    )


async def select_latest_features(
    conn: _DbExecutor,
    *,
    prefix: str | None,
    limit: int,
    offset: int,
) -> list[FeatureRow]:
    """Return latest value per (feature_name, symbol), name-prefix filtered.

    Uses ``features_latest (feature_name, symbol, computed_at DESC)``
    index per §7.2:914 — DISTINCT ON walks the index in column order.
    Caller passes raw prefix string; helper appends ``%`` server-side
    for SQL LIKE via parameter binding (NEVER concatenated into SQL).
    Empty / None prefix → no filter (return all latest pairs).

    NOTE: Pagination is snapshot-deterministic within one query but
    page contents may shift across calls if new (feature_name, symbol)
    pairs appear in features. Acceptable for MVP UI scrolling per
    OQ-3 default; cursor-based pagination deferred to F5+ if perf
    bottleneck surfaces.
    """
    if prefix:
        rows = await conn.fetch(
            _LATEST_FEATURES_WITH_PREFIX_SQL,
            f"{prefix}%",
            limit,
            offset,
        )
    else:
        rows = await conn.fetch(_LATEST_FEATURES_NO_FILTER_SQL, limit, offset)
    return [_row_to_feature(row) for row in rows]


async def count_latest_features(
    conn: _DbExecutor,
    *,
    prefix: str | None,
) -> int:
    """Total count of distinct (feature_name, symbol) pairs matching prefix."""
    if prefix:
        row = await conn.fetchrow(
            _COUNT_LATEST_FEATURES_WITH_PREFIX_SQL,
            f"{prefix}%",
        )
    else:
        row = await conn.fetchrow(_COUNT_LATEST_FEATURES_NO_FILTER_SQL)
    if row is None:
        return 0
    return int(row["n"])


def _build_features_history_where_clause(
    *,
    feature_name: str,
    symbol: str,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Compose WHERE clause for features-history queries.

    Mandatory predicates: ``feature_name = $1 AND symbol = $2`` (UI
    always selects one feature/symbol pair). Optional ``computed_at >=``
    / ``computed_at <`` filters appended as additional `$N` placeholders
    only (NEVER string interpolation per L-008 + §5.10).

    Returns ``(where_clause, [feature_name, symbol, *optional_args])``.
    """
    bind_args: list[Any] = [feature_name, symbol]
    predicates = ["feature_name = $1", "symbol = $2"]
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"computed_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"computed_at < ${len(bind_args)}")
    return ("WHERE " + " AND ".join(predicates), bind_args)


async def select_features_history(
    conn: _DbExecutor,
    *,
    feature_name: str,
    symbol: str,
    from_at: datetime | None,
    to_at: datetime | None,
    limit: int,
    offset: int,
) -> list[FeatureRow]:
    """Return time-series of one (feature_name, symbol) pair.

    ORDER BY ``computed_at DESC`` so most-recent points sort first;
    chart UI reverses for left-to-right time axis. ``from_at`` is
    inclusive, ``to_at`` is exclusive — half-open interval (mirror
    trades.closed_at + signals.received_at convention from T-402/T-403).
    """
    where_clause, where_args = _build_features_history_where_clause(
        feature_name=feature_name,
        symbol=symbol,
        from_at=from_at,
        to_at=to_at,
    )
    limit_placeholder = f"${len(where_args) + 1}"
    offset_placeholder = f"${len(where_args) + 2}"
    sql = (
        f"SELECT {_FEATURES_BASE_COLUMNS} FROM features "  # noqa: S608  # nosec B608
        f"{where_clause} "
        "ORDER BY computed_at DESC "
        f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
    )
    rows = await conn.fetch(sql, *where_args, limit, offset)
    return [_row_to_feature(row) for row in rows]


async def count_features_history(
    conn: _DbExecutor,
    *,
    feature_name: str,
    symbol: str,
    from_at: datetime | None,
    to_at: datetime | None,
) -> int:
    """Total count of features-history rows for one (feature_name, symbol).

    Routes through :func:`_build_features_history_where_clause` so
    filter semantics stay in sync with :func:`select_features_history`
    (no drift between count and page query).
    """
    where_clause, where_args = _build_features_history_where_clause(
        feature_name=feature_name,
        symbol=symbol,
        from_at=from_at,
        to_at=to_at,
    )
    sql = f"SELECT COUNT(*) AS n FROM features {where_clause}"  # noqa: S608  # nosec B608
    row = await conn.fetchrow(sql, *where_args)
    if row is None:
        return 0
    return int(row["n"])


# ---------------------------------------------------------------------------
# T-405 — /api/configs/* + /api/audit/* endpoints (§7.2:861-874 + §7.2:1108-1126)
# ---------------------------------------------------------------------------

from packages.db.queries.audit import (  # noqa: E402 — re-used by audit reader, T-405 inline import for module structure
    AuditEventRow,
    _to_jsonable,
)


@dataclass(frozen=True, slots=True)
class BotConfigRow:
    """Full projection of ``bot_configs`` row (8 fields per §7.2:864-874)."""

    id: int
    bot_id: str
    version: int
    applied_at: datetime
    applied_by: str
    config_yaml: str
    config_hash: str
    notes: str | None


_SELECT_BOT_CONFIG_CURRENT_SQL = """
    SELECT id, bot_id, version, applied_at, applied_by,
           config_yaml, config_hash, notes
    FROM bot_configs
    WHERE bot_id = $1
    ORDER BY version DESC
    LIMIT 1
"""

_SELECT_BOT_CONFIG_VERSIONS_SQL = """
    SELECT id, bot_id, version, applied_at, applied_by,
           config_yaml, config_hash, notes
    FROM bot_configs
    WHERE bot_id = $1
    ORDER BY version DESC
    LIMIT $2 OFFSET $3
"""

_COUNT_BOT_CONFIG_VERSIONS_SQL = """
    SELECT COUNT(*) AS n FROM bot_configs WHERE bot_id = $1
"""

_SELECT_BOT_CONFIG_BY_VERSION_SQL = """
    SELECT id, bot_id, version, applied_at, applied_by,
           config_yaml, config_hash, notes
    FROM bot_configs
    WHERE bot_id = $1 AND version = $2
"""

_SELECT_MAX_BOT_CONFIG_VERSION_SQL = """
    SELECT COALESCE(MAX(version), 0) AS max_version
    FROM bot_configs
    WHERE bot_id = $1
"""

_INSERT_BOT_CONFIG_SQL = """
    INSERT INTO bot_configs (bot_id, version, applied_at, applied_by,
                              config_yaml, config_hash, notes)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    RETURNING id, bot_id, version, applied_at, applied_by,
              config_yaml, config_hash, notes
"""

_UPDATE_BOT_CONFIG_APPLIED_SQL = """
    UPDATE bots
    SET config_hash = $2, config_applied_at = $3
    WHERE bot_id = $1
"""


def _row_to_bot_config(row: asyncpg.Record) -> BotConfigRow:
    return BotConfigRow(
        id=int(row["id"]),
        bot_id=str(row["bot_id"]),
        version=int(row["version"]),
        applied_at=row["applied_at"],
        applied_by=str(row["applied_by"]),
        config_yaml=str(row["config_yaml"]),
        config_hash=str(row["config_hash"]),
        notes=str(row["notes"]) if row["notes"] is not None else None,
    )


async def select_bot_config_current(
    conn: _DbExecutor,
    bot_id: str,
) -> BotConfigRow | None:
    """Return the latest bot_config row for ``bot_id``; ``None`` if no versions yet."""
    row = await conn.fetchrow(_SELECT_BOT_CONFIG_CURRENT_SQL, bot_id)
    return _row_to_bot_config(row) if row is not None else None


async def select_bot_config_versions(
    conn: _DbExecutor,
    *,
    bot_id: str,
    limit: int,
    offset: int,
) -> list[BotConfigRow]:
    """Return paginated bot_config history for ``bot_id``; ORDER BY version DESC."""
    rows = await conn.fetch(_SELECT_BOT_CONFIG_VERSIONS_SQL, bot_id, limit, offset)
    return [_row_to_bot_config(row) for row in rows]


async def count_bot_config_versions(conn: _DbExecutor, bot_id: str) -> int:
    """Total count of bot_config versions for ``bot_id`` (pagination total)."""
    row = await conn.fetchrow(_COUNT_BOT_CONFIG_VERSIONS_SQL, bot_id)
    if row is None:
        return 0
    return int(row["n"])


async def select_bot_config_by_version(
    conn: _DbExecutor,
    *,
    bot_id: str,
    version: int,
) -> BotConfigRow | None:
    """Return one bot_config row by ``(bot_id, version)``; ``None`` if missing."""
    row = await conn.fetchrow(_SELECT_BOT_CONFIG_BY_VERSION_SQL, bot_id, version)
    return _row_to_bot_config(row) if row is not None else None


async def select_max_bot_config_version(
    conn: _DbExecutor,
    bot_id: str,
) -> int:
    """Return the current max version for ``bot_id``; ``0`` when no versions exist.

    Caller computes ``next_version = max + 1`` inside the same tx as
    ``insert_bot_config`` per T-405 WG#3 race policy (race detected via
    UniqueViolation on (bot_id, version), NOT prevented).
    """
    row = await conn.fetchrow(_SELECT_MAX_BOT_CONFIG_VERSION_SQL, bot_id)
    if row is None:
        return 0
    return int(row["max_version"])


@non_idempotent
async def insert_bot_config(
    conn: _DbExecutor,
    *,
    bot_id: str,
    version: int,
    applied_at: datetime,
    applied_by: str,
    config_yaml: str,
    config_hash: str,
    notes: str | None,
) -> BotConfigRow:
    """INSERT new bot_configs row + return it.

    Marked ``@non_idempotent`` per §N3. Raises
    :class:`asyncpg.UniqueViolationError` on duplicate ``(bot_id, version)``
    (concurrent apply race per WG#3) — caller (router) catches and
    returns 409 Conflict.
    """
    row = await conn.fetchrow(
        _INSERT_BOT_CONFIG_SQL,
        bot_id,
        version,
        applied_at,
        applied_by,
        config_yaml,
        config_hash,
        notes,
    )
    if row is None:
        msg = "INSERT ... RETURNING produced no row"
        raise RuntimeError(msg)
    return _row_to_bot_config(row)


@non_idempotent
async def update_bot_config_applied(
    conn: _DbExecutor,
    *,
    bot_id: str,
    config_hash: str,
    config_applied_at: datetime,
) -> bool:
    """UPDATE ``bots`` SET ``config_hash`` + ``config_applied_at``; return True if 1 row affected.

    Marked ``@non_idempotent`` per §N3. False return → caller (router)
    raises ``RuntimeError`` to trigger tx rollback per WG#9 (bot row
    missing during apply — race or FK gap).
    """
    status = await conn.execute(
        _UPDATE_BOT_CONFIG_APPLIED_SQL,
        bot_id,
        config_hash,
        config_applied_at,
    )
    return status.endswith(" 1")


# ---------------------------------------------------------------------------
# audit reader (T-401a's audit_events table; T-405 ships paginated reader)
# ---------------------------------------------------------------------------


_AUDIT_BASE_COLUMNS = (
    "id, occurred_at, actor, action, entity_type, entity_id, "
    "before_state, after_state, correlation_id, meta"
)

_SELECT_AUDIT_BY_PK_SQL = (
    f"SELECT {_AUDIT_BASE_COLUMNS}"  # noqa: S608  # nosec B608
    " FROM audit_events WHERE occurred_at = $1 AND id = $2"
)


def _row_to_audit_event(row: asyncpg.Record) -> AuditEventRow:
    before = row["before_state"]
    after = row["after_state"]
    meta = row["meta"]
    return AuditEventRow(
        id=int(row["id"]),
        occurred_at=row["occurred_at"],
        actor=str(row["actor"]),
        action=str(row["action"]),
        entity_type=str(row["entity_type"]),
        entity_id=str(row["entity_id"]),
        before_state=before if isinstance(before, dict) else None,
        after_state=after if isinstance(after, dict) else None,
        correlation_id=(str(row["correlation_id"]) if row["correlation_id"] is not None else None),
        meta=meta if isinstance(meta, dict) else {},
    )


def _build_audit_where_clause(
    *,
    entity_type: str | None,
    entity_id: str | None,
    actor: str | None,
    action_prefix: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Compose dynamic WHERE clause for audit_events queries.

    Mirror T-402/T-403/T-404 dynamic builder pattern — `$N` placeholders
    only (NEVER string interpolation per L-008 + §5.10). 6 filter slots.
    ``action_prefix`` does ``LIKE $N`` with caller-supplied raw prefix
    appended ``%`` server-side (mirror T-404 select_latest_features
    LIKE pattern).

    Returns ``("", [])`` when all filters None.
    """
    predicates: list[str] = []
    bind_args: list[Any] = []
    if entity_type is not None:
        bind_args.append(entity_type)
        predicates.append(f"entity_type = ${len(bind_args)}")
    if entity_id is not None:
        bind_args.append(entity_id)
        predicates.append(f"entity_id = ${len(bind_args)}")
    if actor is not None:
        bind_args.append(actor)
        predicates.append(f"actor = ${len(bind_args)}")
    if action_prefix is not None:
        bind_args.append(f"{action_prefix}%")
        predicates.append(f"action LIKE ${len(bind_args)}")
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"occurred_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"occurred_at < ${len(bind_args)}")
    if not predicates:
        return ("", [])
    return ("WHERE " + " AND ".join(predicates), bind_args)


async def select_audit_events_paginated(
    conn: _DbExecutor,
    *,
    entity_type: str | None,
    entity_id: str | None,
    actor: str | None,
    action_prefix: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
    limit: int,
    offset: int,
) -> list[AuditEventRow]:
    """Return one page of audit_events with optional filters.

    ORDER BY ``occurred_at DESC, id DESC`` so most-recent first per
    audit-log-viewer convention. ``from_at`` inclusive, ``to_at``
    exclusive (half-open interval mirror T-402/T-403/T-404).
    """
    where_clause, where_args = _build_audit_where_clause(
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        action_prefix=action_prefix,
        from_at=from_at,
        to_at=to_at,
    )
    limit_placeholder = f"${len(where_args) + 1}"
    offset_placeholder = f"${len(where_args) + 2}"
    sql = (
        f"SELECT {_AUDIT_BASE_COLUMNS} FROM audit_events "  # noqa: S608  # nosec B608
        f"{where_clause} "
        "ORDER BY occurred_at DESC, id DESC "
        f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
    )
    rows = await conn.fetch(sql, *where_args, limit, offset)
    return [_row_to_audit_event(row) for row in rows]


async def count_audit_events(
    conn: _DbExecutor,
    *,
    entity_type: str | None,
    entity_id: str | None,
    actor: str | None,
    action_prefix: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> int:
    """Total count of audit_events matching same filters as :func:`select_audit_events_paginated`.

    Routes through :func:`_build_audit_where_clause` (no drift between
    count and page query).
    """
    where_clause, where_args = _build_audit_where_clause(
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        action_prefix=action_prefix,
        from_at=from_at,
        to_at=to_at,
    )
    sql = f"SELECT COUNT(*) AS n FROM audit_events {where_clause}"  # noqa: S608  # nosec B608
    row = await conn.fetchrow(sql, *where_args)
    if row is None:
        return 0
    return int(row["n"])


async def select_audit_event_by_id(
    conn: _DbExecutor,
    *,
    occurred_at: datetime,
    event_id: int,
) -> AuditEventRow | None:
    """Return one audit_event row by composite PK ``(occurred_at, id)``; ``None`` if missing.

    ``occurred_at`` is REQUIRED for hypertable chunk pruning per WG#5
    (audit_events 30-day-chunk hypertable; UI always has occurred_at
    from list response → no UX cost; F5+ retention growth makes chunk-
    pruning structurally correct). Distinct from T-403 select_signal_by_id
    walks-every-chunk pattern.
    """
    row = await conn.fetchrow(_SELECT_AUDIT_BY_PK_SQL, occurred_at, event_id)
    return _row_to_audit_event(row) if row is not None else None


# ---------------------------------------------------------------------------
# T-406 — /api/analytics/* aggregates query helper (§9.6:1628 + §14.3:2060)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TradeRealizedPnlRow:
    """Minimal trades projection for analytics aggregation (3 fields).

    Distinct from T-402 ``TradeRow`` 19-field full projection — analytics
    aggregates only need ``realized_pnl`` + ``closed_at`` + ``bot_id``.
    Smaller projection keeps PG round-trip lean for ~1k-100k rows window.
    Feeds expectancy + heatmap + pnl-series + Monte-Carlo bootstrap.
    """

    realized_pnl: Decimal
    closed_at: datetime
    bot_id: str


def _build_analytics_where_clause(
    *,
    bot_id: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Compose dynamic WHERE clause for analytics queries.

    Mirror T-402/T-403/T-404 pattern — `$N` placeholders only (NEVER
    string interpolation per L-008 + §5.10). 3 filter slots. Note: the
    base predicate ``status = 'closed' AND realized_pnl IS NOT NULL`` is
    inlined at call site (NOT a filter slot — it's the analytics charter
    invariant per OQ semantics: only closed trades with finalized P&L
    count for stats).

    Returns ``("AND <predicates>", [bind args])`` when filters present;
    ``("", [])`` when all filters None (caller appends nothing — base
    WHERE clause is sufficient).
    """
    predicates: list[str] = []
    bind_args: list[Any] = []
    if bot_id is not None:
        bind_args.append(bot_id)
        predicates.append(f"bot_id = ${len(bind_args)}")
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"closed_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"closed_at < ${len(bind_args)}")
    if not predicates:
        return ("", [])
    return ("AND " + " AND ".join(predicates), bind_args)


async def select_trades_for_analytics(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> list[TradeRealizedPnlRow]:
    """Return all closed trades with finalized P&L for the filter window.

    Charter invariant: only ``status = 'closed' AND realized_pnl IS NOT NULL``
    rows count for analytics (per T-406 OQ semantics + brief §9.6:1628 —
    expectancy + heatmap + pnl-series + MC operate on closed P&L only).

    ORDER BY ``closed_at`` ASC for deterministic pnl-series + MC bootstrap
    + heatmap iteration. Limit-less: feeds in-memory aggregation; routers
    enforce reasonable filter windows via UI ``?from=`` / ``?to=`` defaults
    (e.g. /api/analytics/pnl-series pre-validates window per WG#7 cap).
    """
    where_extra, where_args = _build_analytics_where_clause(
        bot_id=bot_id,
        from_at=from_at,
        to_at=to_at,
    )
    sql = (
        "SELECT realized_pnl, closed_at, bot_id FROM trades "  # noqa: S608  # nosec B608
        "WHERE status = 'closed' AND realized_pnl IS NOT NULL "
        f"{where_extra} "
        "ORDER BY closed_at ASC"
    )
    rows = await conn.fetch(sql, *where_args)
    return [
        TradeRealizedPnlRow(
            realized_pnl=row["realized_pnl"],
            closed_at=row["closed_at"],
            bot_id=str(row["bot_id"]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# T-407 — /api/backtests/* trigger + read (§9.6:1629 + §14.3:2063)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BacktestRunRow:
    """Full projection of ``backtest_runs`` row (12 columns per migration 0012).

    Matches §7.2:1144-1156 11-column verbatim plus ``bot_id`` 12th column
    added in migration 0012 for T-415 per-bot historic-runs UI filter.
    ``status`` narrowed via :class:`BacktestStatus` StrEnum (4 values;
    F4 only writes ``QUEUED``, F5+ worker writes the rest — forward-compat
    per T-407 plan).
    """

    id: UUID
    name: str
    bot_id: str
    config_yaml: str
    config_hash: str
    date_range_start: datetime
    date_range_end: datetime
    status: BacktestStatus
    started_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any] | None
    notes: str | None


_BACKTEST_BASE_COLUMNS = (
    "id, name, bot_id, config_yaml, config_hash, "
    "date_range_start, date_range_end, status, "
    "started_at, finished_at, summary, notes"
)

_SELECT_BACKTEST_BY_ID_SQL = (
    f"SELECT {_BACKTEST_BASE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM backtest_runs WHERE id = $1"
)

_INSERT_BACKTEST_RUN_SQL = """
    INSERT INTO backtest_runs (name, bot_id, config_yaml, config_hash,
                               date_range_start, date_range_end, status,
                               started_at, finished_at, summary, notes)
    VALUES ($1, $2, $3, $4, $5, $6, 'queued', $7, NULL, NULL, $8)
    RETURNING id, name, bot_id, config_yaml, config_hash,
              date_range_start, date_range_end, status,
              started_at, finished_at, summary, notes
"""


def _row_to_backtest_run(row: asyncpg.Record) -> BacktestRunRow:
    """Narrow asyncpg row to typed dataclass; BacktestStatus ctor validates enum.

    ``summary`` JSONB is dict-or-None; defensive narrowing ensures non-dict
    values fall back to None (would be a schema violation upstream but
    defensive is cheap).
    """
    summary_value = row["summary"]
    return BacktestRunRow(
        id=row["id"],
        name=str(row["name"]),
        bot_id=str(row["bot_id"]),
        config_yaml=str(row["config_yaml"]),
        config_hash=str(row["config_hash"]),
        date_range_start=row["date_range_start"],
        date_range_end=row["date_range_end"],
        status=BacktestStatus(str(row["status"])),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        summary=summary_value if isinstance(summary_value, dict) else None,
        notes=str(row["notes"]) if row["notes"] is not None else None,
    )


def _build_backtests_where_clause(
    *,
    bot_id: str | None,
    status: BacktestStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> tuple[str, list[Any]]:
    """Compose dynamic WHERE clause for backtest_runs queries.

    Mirror T-402/T-403/T-404/T-405 dynamic builder pattern — `$N`
    placeholders only (NEVER string interpolation per L-008 + §5.10).
    4 filter slots. ``from_at`` inclusive, ``to_at`` exclusive (half-open
    interval mirror) on ``started_at``.

    Returns ``("", [])`` when all filters None.
    """
    predicates: list[str] = []
    bind_args: list[Any] = []
    if bot_id is not None:
        bind_args.append(bot_id)
        predicates.append(f"bot_id = ${len(bind_args)}")
    if status is not None:
        bind_args.append(str(status))
        predicates.append(f"status = ${len(bind_args)}")
    if from_at is not None:
        bind_args.append(from_at)
        predicates.append(f"started_at >= ${len(bind_args)}")
    if to_at is not None:
        bind_args.append(to_at)
        predicates.append(f"started_at < ${len(bind_args)}")
    if not predicates:
        return ("", [])
    return ("WHERE " + " AND ".join(predicates), bind_args)


async def select_backtest_runs_paginated(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    status: BacktestStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
    limit: int,
    offset: int,
) -> list[BacktestRunRow]:
    """Return one page of backtest_runs with optional filters.

    ORDER BY ``started_at DESC`` so newest runs appear first per T-415
    Backtest lab UI convention. ``from_at`` inclusive, ``to_at`` exclusive.
    """
    where_clause, where_args = _build_backtests_where_clause(
        bot_id=bot_id,
        status=status,
        from_at=from_at,
        to_at=to_at,
    )
    limit_placeholder = f"${len(where_args) + 1}"
    offset_placeholder = f"${len(where_args) + 2}"
    sql = (
        f"SELECT {_BACKTEST_BASE_COLUMNS} FROM backtest_runs "  # noqa: S608  # nosec B608
        f"{where_clause} "
        "ORDER BY started_at DESC "
        f"LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
    )
    rows = await conn.fetch(sql, *where_args, limit, offset)
    return [_row_to_backtest_run(row) for row in rows]


async def count_backtest_runs(
    conn: _DbExecutor,
    *,
    bot_id: str | None,
    status: BacktestStatus | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> int:
    """Total count of backtest_runs matching same filters as :func:`select_backtest_runs_paginated`.

    Routes through :func:`_build_backtests_where_clause` (no drift
    between count and page query — helper-sharing pin per T-402 WG#5).
    """
    where_clause, where_args = _build_backtests_where_clause(
        bot_id=bot_id,
        status=status,
        from_at=from_at,
        to_at=to_at,
    )
    sql = f"SELECT COUNT(*) AS n FROM backtest_runs {where_clause}"  # noqa: S608  # nosec B608
    row = await conn.fetchrow(sql, *where_args)
    if row is None:
        return 0
    return int(row["n"])


async def select_backtest_run_by_id(
    conn: _DbExecutor,
    run_id: UUID,
) -> BacktestRunRow | None:
    """Return one backtest_runs row by UUID PK; ``None`` if missing.

    Simple PK lookup — backtest_runs is NOT a hypertable (low-volume
    per T-407 OQ-1=A), so no chunk-pruning concern.
    """
    row = await conn.fetchrow(_SELECT_BACKTEST_BY_ID_SQL, run_id)
    return _row_to_backtest_run(row) if row is not None else None


@non_idempotent
async def insert_backtest_run(
    conn: _DbExecutor,
    *,
    name: str,
    bot_id: str,
    config_yaml: str,
    config_hash: str,
    date_range_start: datetime,
    date_range_end: datetime,
    started_at: datetime,
    notes: str | None,
) -> BacktestRunRow:
    """INSERT one row into ``backtest_runs`` (status='queued') and RETURN it.

    Marked ``@non_idempotent`` per §N3. F4 always writes ``status='queued'``
    (F5+ worker transitions); ``finished_at`` and ``summary`` are NULL at
    insert time. ``id`` populated by ``gen_random_uuid()`` server default
    (pgcrypto extension enabled in migration 0012).
    """
    row = await conn.fetchrow(
        _INSERT_BACKTEST_RUN_SQL,
        name,
        bot_id,
        config_yaml,
        config_hash,
        date_range_start,
        date_range_end,
        started_at,
        notes,
    )
    if row is None:
        msg = "INSERT ... RETURNING produced no row"
        raise RuntimeError(msg)
    return _row_to_backtest_run(row)


# ---------------------------------------------------------------------------
# T-507b — backtest_runs FSM transitions + paper_trades → backtest_trades copy
# (CLI consumer at scripts/backtest.py)
# ---------------------------------------------------------------------------

_UPDATE_BACKTEST_RUN_TO_RUNNING_SQL = """
    UPDATE backtest_runs
       SET status='running',
           started_at=$1
     WHERE id=$2
"""

_UPDATE_BACKTEST_RUN_COMPLETION_SQL = """
    UPDATE backtest_runs
       SET status=$1,
           summary=$2::jsonb,
           finished_at=$3
     WHERE id=$4
"""

_COPY_PAPER_TRADES_TO_BACKTEST_SQL = """
    INSERT INTO backtest_trades (
        run_id, bot_id, signal_id, open_order_id, close_order_id,
        symbol, side, entry_price, exit_price, qty, notional_usd,
        realized_pnl, fees_paid, close_reason, opened_at, closed_at,
        status, mfe_pct, mae_pct, confidence_score, meta
    )
    SELECT
        $1, $2, signal_id, open_order_id, close_order_id,
        symbol, side, entry_price, exit_price, qty, notional_usd,
        realized_pnl, fees_paid, close_reason, opened_at, closed_at,
        status, mfe_pct, mae_pct, confidence_score, meta
    FROM paper_trades
    WHERE bot_id = $2
      AND status = 'closed'
"""


@idempotent
async def update_backtest_run_to_running(
    conn: _DbExecutor,
    *,
    run_id: UUID,
    started_at: datetime,
) -> None:
    """T-507b: transition ``backtest_runs.status`` queued → running.

    ``started_at`` MUST be Python-side ``datetime.now(UTC)`` per §N1
    (no SQL ``NOW()`` / ``CURRENT_TIMESTAMP``; brief line 127 invariant).
    @idempotent: UPDATE WHERE id sets fixed values; replay-safe.
    """
    await conn.execute(_UPDATE_BACKTEST_RUN_TO_RUNNING_SQL, started_at, run_id)


@idempotent
async def update_backtest_run_completion(
    conn: _DbExecutor,
    *,
    run_id: UUID,
    status: BacktestStatus,
    summary: dict[str, Any],
    finished_at: datetime,
    codec_registered: bool = False,
) -> None:
    """T-507b + T-509: transition ``backtest_runs`` to terminal status + persist summary.

    L-013 codec-state-immune via explicit `codec_registered` flag (T-509
    BLOCKER #1 fix per plan-reviewer cycle):

    * ``codec_registered=False`` (default; T-507b CLI invocation path; pool
      created via ``create_pool(...)`` without ``init`` hook): bind
      ``json.dumps(_to_jsonable(summary))`` text-mode + ``$N::jsonb``
      cast. asyncpg without codec rejects raw dict for ``$N::jsonb``.
    * ``codec_registered=True`` (T-509 worker invocation path; analytics-api
      pool registers ``_register_jsonb_codec`` per
      ``services/analytics_api/app/main.py:121``): bind
      ``_to_jsonable(summary)`` dict directly. Codec encoder serializes
      via internal ``json.dumps``; text-mode + codec would double-encode
      (L-011 regression class).

    Backwards-compat: default False matches existing T-507b CLI behavior
    (commit fcdc453); T-509 worker passes True at single dispatch_failed
    call site.
    """
    bind_value: Any = (
        _to_jsonable(summary) if codec_registered else json.dumps(_to_jsonable(summary))
    )
    await conn.execute(
        _UPDATE_BACKTEST_RUN_COMPLETION_SQL,
        status.value,
        bind_value,
        finished_at,
        run_id,
    )


@non_idempotent
async def copy_paper_trades_to_backtest(
    conn: _DbExecutor,
    *,
    run_id: UUID,
    bot_id: str,
) -> int:
    """T-507b OQ-3=A: post-replay SQL copy ``paper_trades`` → ``backtest_trades``.

    Per OQ-D=C Belt-and-suspenders: NO time-window filter — replay-clock
    makes paper trade timestamps historical, but defensive against
    clock-edge cases (e.g., last-candle SL fires just after to_at).
    Operator-discipline: run backtest against fresh / truncated
    ``paper_trades``, OR accept that prior-run trades for same bot could
    be re-copied (cross-bot leakage prevented by ``WHERE bot_id = $2``).

    Returns INSERT row count. ``@non_idempotent`` because re-running
    re-copies same rows (no UNIQUE constraint on
    ``backtest_trades.(run_id, signal_id)`` per migration 0013).
    """
    result = await conn.execute(
        _COPY_PAPER_TRADES_TO_BACKTEST_SQL,
        run_id,
        bot_id,
    )
    # asyncpg returns "INSERT 0 N" command tag.
    return int(result.split()[-1])


# ---------------------------------------------------------------------------
# T-508 — `--compare run_A run_B` mode read helpers (BRIEF §12.2:1969-1970)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DivergingTradeRow:
    """Per-trade diff projection for `--compare`: signal_id + (close_reason,
    realized_pnl) for both runs. Per OQ-2=A diff scope is close_reason +
    realized_pnl ONLY.
    """

    signal_id: int
    a_close_reason: str | None
    a_realized_pnl: Decimal | None
    b_close_reason: str | None
    b_realized_pnl: Decimal | None


_SELECT_BACKTEST_RUN_SUMMARY_SQL = """
    SELECT summary FROM backtest_runs WHERE id = $1
"""

_SELECT_DIVERGING_TRADES_SQL = """
    SELECT
        a.signal_id,
        a.close_reason AS a_close_reason,
        a.realized_pnl AS a_realized_pnl,
        b.close_reason AS b_close_reason,
        b.realized_pnl AS b_realized_pnl
    FROM backtest_trades a
    INNER JOIN backtest_trades b ON a.signal_id = b.signal_id
    WHERE a.run_id = $1
      AND b.run_id = $2
      AND a.signal_id IS NOT NULL
      AND (
          COALESCE(a.close_reason, '') != COALESCE(b.close_reason, '')
          OR a.realized_pnl IS DISTINCT FROM b.realized_pnl
      )
    ORDER BY a.signal_id
"""

_COUNT_COMMON_SIGNALS_SQL = """
    SELECT COUNT(*) AS n
    FROM backtest_trades a
    INNER JOIN backtest_trades b ON a.signal_id = b.signal_id
    WHERE a.run_id = $1
      AND b.run_id = $2
      AND a.signal_id IS NOT NULL
"""


async def select_backtest_run_summary(
    conn: _DbExecutor,
    *,
    run_id: UUID,
) -> dict[str, Any] | None:
    """T-508: Return parsed ``backtest_runs.summary`` JSONB for run_id, or None.

    Read-only; no idempotency decorator (matches existing read-helper convention
    `select_backtest_run_by_id`). Returns None on missing row OR NULL summary;
    caller (CLI) emits SystemExit(1) with operator-actionable message.

    L-013 read-side N/A: CLI does NOT register JSONB codec; asyncpg returns
    summary as `str` (text-mode); helper parses via `json.loads` to return
    dict per public API contract. (Codec-registered consumer like
    analytics-api would receive dict directly — handled defensively.)
    """
    row = await conn.fetchrow(_SELECT_BACKTEST_RUN_SUMMARY_SQL, run_id)
    if row is None or row["summary"] is None:
        return None
    summary = row["summary"]
    if isinstance(summary, str):
        return cast("dict[str, Any]", json.loads(summary))
    return cast("dict[str, Any]", summary)


async def select_diverging_trades_for_compare(
    conn: _DbExecutor,
    *,
    run_a: UUID,
    run_b: UUID,
) -> list[DivergingTradeRow]:
    """T-508 OQ-2=A: SELECT JOIN backtest_trades by signal_id where outcome differs.

    Outcome scope per OQ-2=A: close_reason OR realized_pnl. Decimal exact
    equality (no tolerance). ``IS DISTINCT FROM`` handles NULL semantics
    correctly (NULL == NULL via IS DISTINCT FROM; NULL != NULL via plain !=).

    Read-only; no idempotency decorator. Returns list of DivergingTradeRow
    ordered by signal_id ascending.
    """
    rows = await conn.fetch(_SELECT_DIVERGING_TRADES_SQL, run_a, run_b)
    return [
        DivergingTradeRow(
            signal_id=int(r["signal_id"]),
            a_close_reason=r["a_close_reason"],
            a_realized_pnl=(
                Decimal(r["a_realized_pnl"]) if r["a_realized_pnl"] is not None else None
            ),
            b_close_reason=r["b_close_reason"],
            b_realized_pnl=(
                Decimal(r["b_realized_pnl"]) if r["b_realized_pnl"] is not None else None
            ),
        )
        for r in rows
    ]


async def count_common_signals_for_compare(
    conn: _DbExecutor,
    *,
    run_a: UUID,
    run_b: UUID,
) -> int:
    """T-508 WG#3: count common signal_ids between two runs (M for "N of M diverged").

    M=0 → CLI emits "No common signals between run_A and run_B" message
    instead of misleading "N of 0 common signals diverged" header.
    """
    row = await conn.fetchrow(_COUNT_COMMON_SIGNALS_SQL, run_a, run_b)
    if row is None:
        return 0
    return int(row["n"])


# ---------------------------------------------------------------------------
# T-509 — backtest worker claim helper (analytics-api lifespan task)
# ---------------------------------------------------------------------------

_CLAIM_NEXT_BACKTEST_RUN_SQL = """
    UPDATE backtest_runs
       SET status='running',
           started_at=$1
     WHERE id IN (
        SELECT id FROM backtest_runs
         WHERE status='queued'
         ORDER BY created_at
         LIMIT 1
         FOR UPDATE SKIP LOCKED
     )
    RETURNING id, name, bot_id, config_yaml, config_hash,
              date_range_start, date_range_end, status,
              started_at, finished_at, summary, notes, created_at
"""


@non_idempotent
async def claim_next_backtest_run(
    conn: _DbExecutor,
    *,
    started_at: datetime,
) -> BacktestRunRow | None:
    """T-509 OQ-2=A: atomic UPDATE...RETURNING + SKIP LOCKED claim.

    Race-safe for future multi-worker (FOR UPDATE SKIP LOCKED ensures
    multiple workers don't claim same row). Single-worker today: SKIP
    LOCKED is no-op. Returns None when no queued rows.

    @non_idempotent because state transition queued→running is one-shot;
    re-running on same row would no-op (status filter excludes it).

    `started_at` populated Python-side per §N1 (no SQL NOW()).
    """
    row = await conn.fetchrow(_CLAIM_NEXT_BACKTEST_RUN_SQL, started_at)
    if row is None:
        return None
    return _row_to_backtest_run(row)


# ---------------------------------------------------------------------------
# T-501 — backtest_trades read helper (§12.2:1969-1971, §7.2:983-1009)
# ---------------------------------------------------------------------------
#
# Read-only helper feeds T-516 per-trade variants drill-down. Write helpers
# (insert_backtest_trade / update_backtest_trade_close) are owned by T-507
# (CLI persists per-trade) + T-509 (worker writes summary aggregates) per
# T-501 plan-doc §Out-of-scope. L-011 forward-pointer there: T-507/T-509
# write helpers MUST pass dicts directly to asyncpg (no json.dumps at call
# site) because analytics-api lifespan registers _register_jsonb_codec.


@dataclass(frozen=True, slots=True)
class BacktestTradeRow:
    """Full projection of ``backtest_trades`` row (22 columns per migration 0013).

    Mirrors live :class:`TradeRow` shape with backtest adaptations:

    * ``run_id`` UUID FK to ``backtest_runs.id`` (cascade-delete per OQ-3).
    * ``open_order_id`` / ``close_order_id`` nullable, no FK (paper backtest
      doesn't write live ``orders`` table).
    * ``status`` is plain ``str`` (not :class:`TradeStatus` StrEnum) — backtest
      domain may add ``'replay-error'`` etc. without touching the live enum.

    Decimal preserved for monetary precision (§5.3); float for stats fields
    (mfe/mae/confidence_score) per live precedent.
    """

    id: int
    run_id: UUID
    bot_id: str
    signal_id: int | None
    open_order_id: int | None
    close_order_id: int | None
    symbol: str
    side: str
    entry_price: Decimal
    exit_price: Decimal | None
    qty: Decimal
    notional_usd: Decimal
    realized_pnl: Decimal | None
    fees_paid: Decimal | None
    close_reason: str | None
    opened_at: datetime
    closed_at: datetime | None
    status: str
    mfe_pct: float | None
    mae_pct: float | None
    confidence_score: float | None
    meta: dict[str, Any]


_BACKTEST_TRADE_COLUMNS = (
    "id, run_id, bot_id, signal_id, open_order_id, close_order_id, "
    "symbol, side, entry_price, exit_price, qty, notional_usd, "
    "realized_pnl, fees_paid, close_reason, opened_at, closed_at, "
    "status, mfe_pct, mae_pct, confidence_score, meta"
)

_SELECT_TRADES_BY_RUN_SQL = (
    f"SELECT {_BACKTEST_TRADE_COLUMNS}"  # noqa: S608 — column whitelist constant, no user input  # nosec B608
    " FROM backtest_trades WHERE run_id = $1 ORDER BY opened_at ASC LIMIT $2 OFFSET $3"
)


def _row_to_backtest_trade(row: asyncpg.Record) -> BacktestTradeRow:
    """Narrow asyncpg row to typed dataclass.

    NUMERIC stĺpce (entry_price/exit_price/qty/notional_usd/realized_pnl/
    fees_paid) sa pass-through ako Decimal — asyncpg NUMERIC codec
    natívne vracia ``decimal.Decimal`` (mirror :func:`_row_to_trade`
    precedent line 489-494). DOUBLE PRECISION stĺpce sa cast-ujú cez
    ``float()`` pre type-narrow (mirror :func:`_row_to_trade`).
    ``meta`` JSONB defensive-narrow: dict-or-empty (asyncpg s
    registered codec vracia dict; mock-based test fixtures môžu vrátiť
    raw dict literal).
    """
    meta_value = row["meta"]
    return BacktestTradeRow(
        id=int(row["id"]),
        run_id=row["run_id"],
        bot_id=str(row["bot_id"]),
        signal_id=int(row["signal_id"]) if row["signal_id"] is not None else None,
        open_order_id=int(row["open_order_id"]) if row["open_order_id"] is not None else None,
        close_order_id=int(row["close_order_id"]) if row["close_order_id"] is not None else None,
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        qty=row["qty"],
        notional_usd=row["notional_usd"],
        realized_pnl=row["realized_pnl"],
        fees_paid=row["fees_paid"],
        close_reason=str(row["close_reason"]) if row["close_reason"] is not None else None,
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        status=str(row["status"]),
        mfe_pct=float(row["mfe_pct"]) if row["mfe_pct"] is not None else None,
        mae_pct=float(row["mae_pct"]) if row["mae_pct"] is not None else None,
        confidence_score=(
            float(row["confidence_score"]) if row["confidence_score"] is not None else None
        ),
        meta=meta_value if isinstance(meta_value, dict) else {},
    )


async def select_trades_by_run(
    conn: _DbExecutor,
    *,
    run_id: UUID,
    limit: int = 100,
    offset: int = 0,
) -> list[BacktestTradeRow]:
    """Return paginated trades for a backtest run (ORDER BY opened_at ASC).

    Default ``limit=100`` covers typical backtest output (<500 trades per
    run); T-516 UI may paginate further. ASC ordering matches
    chronological replay sequence — operator scrolling through a backtest
    sees trades in the order they were generated by the strategy.
    """
    rows = await conn.fetch(_SELECT_TRADES_BY_RUN_SQL, run_id, limit, offset)
    return [_row_to_backtest_trade(row) for row in rows]
