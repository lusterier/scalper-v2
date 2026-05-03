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

``BotStatus`` / ``ExchangeMode`` / ``ExchangeSource`` / ``TradeStatus``
enum narrowing uses canonical :mod:`packages.core.types` StrEnums; the
StrEnum constructor itself raises :class:`ValueError` on unknown
values, so no hand-rolled validator is needed (cleaner than promoting
the private ``_validate_exchange_mode`` from
:mod:`packages.db.queries.execution` per T-401a WG#2 plan-reviewer
alternative + T-401b WG#1 + T-402 WG#1 consistency).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.core import non_idempotent
from packages.core.types import (
    Action,
    BotStatus,
    ExchangeMode,
    ExchangeSource,
    IngestionStatus,
    ScoringDecision,
    TradeStatus,
)

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]

__all__ = [
    "BotDetailRow",
    "FeatureRow",
    "OpenPositionRow",
    "ScoringEvaluationRow",
    "SignalRow",
    "SymbolMapRow",
    "TradeRow",
    "count_features_history",
    "count_latest_features",
    "count_signals",
    "count_trades",
    "delete_symbol_map_entry",
    "insert_symbol_map_entry",
    "select_all_bots",
    "select_all_symbol_map_entries",
    "select_bot_by_id",
    "select_features_history",
    "select_latest_features",
    "select_open_positions",
    "select_scoring_evaluations_by_signal_id",
    "select_signal_by_id",
    "select_signals_paginated",
    "select_symbol_map_entry",
    "select_trade_by_id",
    "select_trades_paginated",
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
