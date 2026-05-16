"""execution-service query module (§5.10, §7.2).

T-215 ships :func:`select_active_bots` for adapter pool composition.
T-216b extends with placement-tx persistence helpers per §9.5 step 8:

* :func:`insert_order` — orders row INSERT (BIGSERIAL id returned).
* :func:`insert_trade` — trades row INSERT (BIGSERIAL id returned;
  ``realized_pnl`` + ``fees_paid`` NULL initial; T-218/T-219 backfill).
* :func:`insert_position_state` — composite PK ``(bot_id, symbol)``.
* :func:`insert_trading_event` — trading_events hypertable INSERT (§7.2 line 1091).
* :func:`update_trade_close` — UPDATE trades SET status='closed' (H-018 PK-only).
* :func:`delete_position_state` — composite PK delete (flat after close).

Mirror T-213b ``packages/exchange/paper/persistence.py`` pattern.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from packages.core import non_idempotent

if TYPE_CHECKING:
    from datetime import datetime

    import asyncpg
    from asyncpg.pool import PoolConnectionProxy

    from packages.core import TradeLifecycleState

    type _DbExecutor = asyncpg.Connection[asyncpg.Record] | PoolConnectionProxy[asyncpg.Record]


__all__ = [
    "BotRow",
    "ExchangeMode",
    "OrderLookupRow",
    "PositionStateRow",
    "TradeLookupRow",
    "delete_position_state",
    "insert_execution",
    "insert_order",
    "insert_position_state",
    "insert_trade",
    "insert_trade_pnl_delta",
    "insert_trading_event",
    "select_active_bots",
    "select_open_order_id_by_trade_id",
    "select_order_id_by_exchange_id",
    "select_order_meta_by_id",
    "select_position_state",
    "select_position_states_for_bots",
    "select_realized_pnl_sum_for_bots_since",
    "select_recent_open_trade_exists",
    "select_trade_by_close_order_id",
    "select_trade_by_open_order_id",
    "select_trade_fsm_params",
    "update_position_state_after_fill",
    "update_position_state_monitor_tick",
    "update_position_state_sl",
    "update_trade_close",
    "update_trade_fees_incremental",
    "update_trade_lifecycle_state",
]


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


# T-216b — placement-tx persistence helpers (§9.5 step 8) ----------------


@non_idempotent
async def insert_order(
    conn: _DbExecutor,
    *,
    bot_id: str,
    signal_id: int | None,
    correlation_id: str,
    exchange_order_id: str,
    exchange: str,
    symbol: str,
    side: Literal["buy", "sell"],
    order_type: str,
    qty: Decimal,
    price: Decimal | None,
    status: str,
    requested_at: datetime,
    filled_at: datetime | None,
    closed_at: datetime | None,
    idempotent_flag: bool,
) -> int:
    """INSERT into ``orders``; return generated BIGSERIAL ``id``.

    ``status`` per §7.2 line 967 enum: ``'requested' | 'placed' | 'filled' |
    'cancelled' | 'rejected' | 'emergency_closed'``. ``idempotent_flag``
    per §N3 marker mapping (market = False per H-003; sl/tp synthetic = True).
    """
    row = await conn.fetchrow(
        """
        INSERT INTO orders (
            bot_id, signal_id, correlation_id, exchange_order_id, exchange,
            symbol, side, order_type, qty, price, status,
            requested_at, filled_at, closed_at, idempotent, meta
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, '{}'::jsonb)
        RETURNING id
        """,
        bot_id,
        signal_id,
        correlation_id,
        exchange_order_id,
        exchange,
        symbol,
        side,
        order_type,
        qty,
        price,
        status,
        requested_at,
        filled_at,
        closed_at,
        idempotent_flag,
    )
    if row is None:
        msg = "INSERT orders ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])


@non_idempotent
async def insert_trade(
    conn: _DbExecutor,
    *,
    bot_id: str,
    signal_id: int | None,
    open_order_id: int,
    symbol: str,
    side: Literal["buy", "sell"],
    entry_price: Decimal,
    qty: Decimal,
    notional_usd: Decimal,
    opened_at: datetime,
    meta: dict[str, Any] | None = None,
) -> int:
    """INSERT into ``trades`` (status='open'); return BIGSERIAL ``id``.

    ``realized_pnl`` and ``fees_paid`` NULL initial (T-218/T-219 backfill
    from execution stream + cumulative-delta close per H-012).

    T-217a extension: ``meta`` kwarg accepts a dict (e.g., FSM runtime params
    ``be_trigger`` / ``be_sl_level`` / ``trail_pct`` from ``OrderRequest``).
    Decimals serialize as strings via ``json.dumps(meta, default=str)`` so
    Pydantic's Decimal-as-string convention is preserved end-to-end. When
    omitted, the column is written as ``'{}'::jsonb`` per T-216b1-shipped behavior
    (preserves the ``emergency_close`` no-meta path; emergency-closed trades
    are not monitored so they carry no FSM params).
    """
    meta_json = "{}" if meta is None else json.dumps(meta, default=str)
    row = await conn.fetchrow(
        """
        INSERT INTO trades (
            bot_id, signal_id, open_order_id, symbol, side, entry_price,
            qty, notional_usd, opened_at, status, meta
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'open', $10::jsonb)
        RETURNING id
        """,
        bot_id,
        signal_id,
        open_order_id,
        symbol,
        side,
        entry_price,
        qty,
        notional_usd,
        opened_at,
        meta_json,
    )
    if row is None:
        msg = "INSERT trades ... RETURNING id produced no row"
        raise RuntimeError(msg)
    return int(row["id"])


@non_idempotent
async def insert_position_state(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    trade_id: int,
    side: Literal["buy", "sell"],
    entry_price: Decimal,
    qty: Decimal,
    remaining_qty: Decimal,
    sl_price: Decimal | None,
    tp_price: Decimal | None,
    sl_type: str | None,
    updated_at: datetime,
) -> None:
    """INSERT into ``position_state`` (composite PK ``(bot_id, symbol)``)."""
    await conn.execute(
        """
        INSERT INTO position_state (
            bot_id, symbol, trade_id, side, entry_price, qty, remaining_qty,
            sl_price, tp_price, sl_type, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        bot_id,
        symbol,
        trade_id,
        side,
        entry_price,
        qty,
        remaining_qty,
        sl_price,
        tp_price,
        sl_type,
        updated_at,
    )


@non_idempotent
async def insert_trading_event(
    conn: _DbExecutor,
    *,
    occurred_at: datetime,
    bot_id: str | None,
    correlation_id: str | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """INSERT into ``trading_events`` hypertable (§7.2 line 1091)."""
    await conn.execute(
        """
        INSERT INTO trading_events (occurred_at, bot_id, correlation_id, event_type, payload)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        occurred_at,
        bot_id,
        correlation_id,
        event_type,
        json.dumps(payload),
    )


async def update_trade_close(
    conn: _DbExecutor,
    *,
    trade_id: int,
    exit_price: Decimal,
    realized_pnl: Decimal,
    fees_paid: Decimal | None,
    closed_at: datetime,
    close_reason: str,
    close_order_id: int,
) -> None:
    """UPDATE trades SET ... WHERE id PK — H-018 PK-only invariant.

    T-219 partial-update: ``fees_paid=None`` omits the column from SET clause
    (preserves existing value — typically the running incremental sum from
    T-218b's ``update_trade_fees_incremental``). ``fees_paid=Decimal(...)``
    writes the value explicitly (T-216b1 ``emergency_close`` callers pass
    ``Decimal('0')`` placeholder per H-012 — preserved unchanged).
    """
    if fees_paid is None:
        await conn.execute(
            """
            UPDATE trades
            SET exit_price = $1, realized_pnl = $2,
                closed_at = $3, close_reason = $4, close_order_id = $5,
                status = 'closed'
            WHERE id = $6
            """,
            exit_price,
            realized_pnl,
            closed_at,
            close_reason,
            close_order_id,
            trade_id,
        )
    else:
        await conn.execute(
            """
            UPDATE trades
            SET exit_price = $1, realized_pnl = $2, fees_paid = $3,
                closed_at = $4, close_reason = $5, close_order_id = $6,
                status = 'closed'
            WHERE id = $7
            """,
            exit_price,
            realized_pnl,
            fees_paid,
            closed_at,
            close_reason,
            close_order_id,
            trade_id,
        )


async def update_trade_lifecycle_state(
    conn: _DbExecutor,
    *,
    trade_id: int,
    state: TradeLifecycleState,
) -> None:
    """UPDATE trades SET lifecycle_state WHERE id PK — H-018 PK-only invariant.

    T-533b1 forward-write primitive for the observable ``lifecycle_state``
    (T-533a additive column; T-533 OQ-1=A — the legacy 4-column state
    [``status`` / ``close_reason`` / ``position_state`` flags] stays
    authoritative for every read/decision; this is observability-only,
    zero behavior change). T-533b2 wires this into every post-trades-row
    state-transition site (direct enum literal) alongside the unchanged
    legacy write; NO caller here (foundation leaf).

    UNDECORATED: an idempotent-by-construction PK-``SET``-column UPDATE —
    verbatim-mirror sibling :func:`update_trade_close` /
    :func:`update_trade_fees_incremental` / :func:`update_position_state_sl`
    (the repo convention: only INSERT / place-order non-idempotent writers
    carry ``@non_idempotent``; §N3 "every external write classified" is
    satisfied here by the PK-SET idempotency — re-running writes the
    identical value, retry-safe). A marker-mirror test pins
    ``is_idempotent`` and ``is_non_idempotent`` both ``False``.

    L-021 audit: ``$1`` is direct column assignment (``SET lifecycle_state
    = $1`` — TEXT column-direct) and ``$2`` is ``WHERE id = $2`` (bigint
    PK column-direct equality) — both L-021-safe column-direct contexts;
    no ``::type`` cast needed; no ``$N`` in arithmetic / CASE /
    function-arg position.
    """
    await conn.execute(
        "UPDATE trades SET lifecycle_state = $1 WHERE id = $2",
        state.value,
        trade_id,
    )


async def select_order_meta_by_id(
    conn: _DbExecutor,
    order_id: int,
) -> tuple[str, str] | None:
    """Return ``(correlation_id, exchange_order_id)`` from orders by id PK; None if missing.

    T-219 reconcile_close uses this to thread correlation_id + exchange_order_id
    into ``OrderClosed`` envelope (per ADR-0006 D5 + Gate-1 BLOCKER #2 / CONCERN #4
    fixes). Pure parametrized SELECT-WHERE-PK; no CAST/COALESCE/CASE per L-008
    active control.
    """
    row = await conn.fetchrow(
        "SELECT correlation_id, exchange_order_id FROM orders WHERE id = $1",
        order_id,
    )
    if row is None:
        return None
    return str(row["correlation_id"]), str(row["exchange_order_id"])


async def delete_position_state(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
) -> None:
    """DELETE from position_state — composite PK ``(bot_id, symbol)``."""
    await conn.execute(
        "DELETE FROM position_state WHERE bot_id = $1 AND symbol = $2",
        bot_id,
        symbol,
    )


# T-218a — execution dispatcher query helpers (§9.5 line 1591) ---------------


@dataclass(frozen=True, slots=True)
class OrderLookupRow:
    """Read-only projection from :func:`select_order_id_by_exchange_id`."""

    id: int


@dataclass(frozen=True, slots=True)
class TradeLookupRow:
    """Read-only projection from open/close-order trade lookup helpers."""

    id: int
    open_order_id: int
    close_order_id: int | None
    side: Literal["buy", "sell"]


@dataclass(frozen=True, slots=True)
class PositionStateRow:
    """Read-only projection from :func:`select_position_state`.

    ``sl_type`` stays loose ``str | None`` (read robustness — DB could in
    theory hold legacy/repair values; we don't want a Literal narrowing
    to barf on read). Write-side helpers (``update_position_state_after_fill``)
    tighten to ``Literal["protective", "be", "trail"] | None`` so a
    typo at the write call-site fails compile-time.
    """

    bot_id: str
    symbol: str
    trade_id: int
    side: Literal["buy", "sell"]
    entry_price: Decimal
    qty: Decimal
    remaining_qty: Decimal
    sl_price: Decimal | None
    tp_price: Decimal | None
    sl_type: str | None
    best_price: Decimal | None = None
    mfe_price: Decimal | None = None
    mae_price: Decimal | None = None
    running_pnl: Decimal = Decimal("0")


async def select_order_id_by_exchange_id(
    conn: _DbExecutor,
    exchange_order_id: str,
) -> int | None:
    """Return ``orders.id`` where ``exchange_order_id`` matches, else None.

    T-218b dispatcher distinguishes (a) fills against open/close orders
    we placed (orders row exists) from (b) synthetic SL/TP/trail fills
    triggered by Bybit when ``set_trading_stop`` SL/TP fires (no orders
    row — H-024 motivation).
    """
    row = await conn.fetchrow(
        "SELECT id FROM orders WHERE exchange_order_id = $1",
        exchange_order_id,
    )
    return None if row is None else int(row["id"])


async def select_trade_by_open_order_id(
    conn: _DbExecutor,
    open_order_id: int,
) -> TradeLookupRow | None:
    """Return open-order trade row keyed by ``open_order_id``, else None."""
    row = await conn.fetchrow(
        """
        SELECT id, open_order_id, close_order_id, side
        FROM trades
        WHERE open_order_id = $1
        """,
        open_order_id,
    )
    if row is None:
        return None
    return TradeLookupRow(
        id=int(row["id"]),
        open_order_id=int(row["open_order_id"]),
        close_order_id=None if row["close_order_id"] is None else int(row["close_order_id"]),
        side=row["side"],
    )


async def select_trade_by_close_order_id(
    conn: _DbExecutor,
    close_order_id: int,
) -> TradeLookupRow | None:
    """Return closed-trade row keyed by ``close_order_id``, else None."""
    row = await conn.fetchrow(
        """
        SELECT id, open_order_id, close_order_id, side
        FROM trades
        WHERE close_order_id = $1
        """,
        close_order_id,
    )
    if row is None:
        return None
    return TradeLookupRow(
        id=int(row["id"]),
        open_order_id=int(row["open_order_id"]),
        close_order_id=int(row["close_order_id"]),
        side=row["side"],
    )


async def select_open_order_id_by_trade_id(
    conn: _DbExecutor,
    trade_id: int,
) -> int | None:
    """Return ``trades.open_order_id`` given trades.id PK; None if trade missing.

    T-218b dispatcher uses this in the synthetic SL/TP/trail fill path
    where ``executions.order_id`` is NOT NULL FK and we have no orders
    row issued for the synthetic fill — the FK is attributed to the
    entry order context (the order that opened the position the
    synthetic fill is closing). Defensible: every fill is anchored to
    a placed-order context even if the fill itself wasn't issued
    against that order.
    """
    row = await conn.fetchrow(
        "SELECT open_order_id FROM trades WHERE id = $1",
        trade_id,
    )
    return None if row is None else int(row["open_order_id"])


async def select_position_state(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
) -> PositionStateRow | None:
    """Return current ``position_state`` row for ``(bot_id, symbol)`` PK or None.

    T-217a extension: SELECT now reads 4 monitor fields (``best_price``,
    ``mfe_price``, ``mae_price``, ``running_pnl``) consumed by the
    PositionLifecycle FSM. T-217b extends with ``tp_hit`` + ``trailing_active``.
    """
    row = await conn.fetchrow(
        """
        SELECT bot_id, symbol, trade_id, side, entry_price, qty, remaining_qty,
               sl_price, tp_price, sl_type,
               best_price, mfe_price, mae_price, running_pnl
        FROM position_state
        WHERE bot_id = $1 AND symbol = $2
        """,
        bot_id,
        symbol,
    )
    if row is None:
        return None
    return PositionStateRow(
        bot_id=row["bot_id"],
        symbol=row["symbol"],
        trade_id=int(row["trade_id"]),
        side=row["side"],
        entry_price=row["entry_price"],
        qty=row["qty"],
        remaining_qty=row["remaining_qty"],
        sl_price=row["sl_price"],
        tp_price=row["tp_price"],
        sl_type=row["sl_type"],
        best_price=row["best_price"],
        mfe_price=row["mfe_price"],
        mae_price=row["mae_price"],
        running_pnl=row["running_pnl"],
    )


_SELECT_POSITION_STATES_FOR_BOTS_SQL = """
    SELECT bot_id, symbol, trade_id, side, entry_price, qty, remaining_qty,
           sl_price, tp_price, sl_type,
           best_price, mfe_price, mae_price, running_pnl
    FROM position_state
    WHERE bot_id = ANY($1::text[])
    ORDER BY bot_id, symbol
"""


async def select_position_states_for_bots(
    conn: _DbExecutor,
    bot_ids: list[str],
) -> list[PositionStateRow]:
    """Return all open ``position_state`` rows for the given bot_ids.

    T-221 post-restart reconciliation reads every bot's open positions in
    one round-trip per pool connection. Empty ``bot_ids`` short-circuits
    to ``[]`` without DB roundtrip (defensive against an empty adapter
    pool — startup before any bot is configured). Result is ordered by
    ``(bot_id, symbol)`` for deterministic logging during reconcile.
    """
    if not bot_ids:
        return []
    rows = await conn.fetch(_SELECT_POSITION_STATES_FOR_BOTS_SQL, bot_ids)
    return [
        PositionStateRow(
            bot_id=row["bot_id"],
            symbol=row["symbol"],
            trade_id=int(row["trade_id"]),
            side=row["side"],
            entry_price=row["entry_price"],
            qty=row["qty"],
            remaining_qty=row["remaining_qty"],
            sl_price=row["sl_price"],
            tp_price=row["tp_price"],
            sl_type=row["sl_type"],
            best_price=row["best_price"],
            mfe_price=row["mfe_price"],
            mae_price=row["mae_price"],
            running_pnl=row["running_pnl"],
        )
        for row in rows
    ]


_SELECT_RECENT_OPEN_TRADE_EXISTS_SQL = """
    SELECT 1 FROM trades
    WHERE bot_id = $1 AND symbol = $2 AND opened_at >= $3 AND status = 'open'
    LIMIT 1
"""


async def select_recent_open_trade_exists(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    since: datetime,
) -> bool:
    """Return True if any ``trades`` row exists for ``(bot_id, symbol)``
    with ``opened_at >= since`` AND ``status = 'open'``.

    T-221 H-026 race-window guard: before market-closing an exchange
    orphan (position on exchange but no ``position_state`` row in DB),
    check whether a placement is in flight for this ``(bot_id, symbol)``.
    The ``>=`` predicate is verbatim — boundary tick at exact ``since``
    counts as "in race window" (test pin per Fixture D).

    Note: only ``status='open'`` trades match. A closed trade in the
    last race-window seconds does NOT indicate an in-flight placement.
    """
    row = await conn.fetchrow(
        _SELECT_RECENT_OPEN_TRADE_EXISTS_SQL,
        bot_id,
        symbol,
        since,
    )
    return row is not None


async def update_position_state_after_fill(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    trade_id: int,
    qty_delta: Decimal,
    new_sl_type: Literal["protective", "be", "trail"] | None,
    updated_at: datetime,
) -> int:
    """Subtract ``qty_delta`` from remaining_qty; optionally set ``sl_type``.

    Composite PK ``(bot_id, symbol)`` plus ``trade_id`` in WHERE clause
    per T-217c / H-033 — guards against composite-PK row identity churn
    under close→reopen race (same `(bot_id, symbol)` reused across
    sequential trades). T-218b body invokes after each execution event:
    ``new_sl_type=None`` keeps existing sl_type (open / close / sl /
    trail fills); ``new_sl_type='trail'`` applies on partial_tp per
    OQ-5 trailing-on-TP-hit.

    Returns rows updated count (parsed from asyncpg command tag).
    Caller MUST check ``== 0`` for trade_id mismatch detection — H-033 /
    T-217c: 0 rows updated means the position_state row's trade_id no
    longer matches the derived trade_id (late WS event arrived after
    close→reopen race; T2's row exists in place of T1's row).

    Write-side ``new_sl_type`` is tightened to ``Literal[...] | None``
    (mypy-narrowed) so typos at the call-site fail compile-time. Read
    projection (:class:`PositionStateRow.sl_type`) stays loose ``str | None``.
    """
    if new_sl_type is None:
        result = await conn.execute(
            """
            UPDATE position_state
            SET remaining_qty = remaining_qty - $1, updated_at = $2
            WHERE bot_id = $3 AND symbol = $4 AND trade_id = $5
            """,
            qty_delta,
            updated_at,
            bot_id,
            symbol,
            trade_id,
        )
    else:
        result = await conn.execute(
            """
            UPDATE position_state
            SET remaining_qty = remaining_qty - $1, sl_type = $2, updated_at = $3
            WHERE bot_id = $4 AND symbol = $5 AND trade_id = $6
            """,
            qty_delta,
            new_sl_type,
            updated_at,
            bot_id,
            symbol,
            trade_id,
        )
    # asyncpg returns "UPDATE <n>" command tag for UPDATE statements.
    return int(result.split()[-1])


async def update_trade_fees_incremental(
    conn: _DbExecutor,
    *,
    trade_id: int,
    fee_delta: Decimal,
) -> None:
    """Incremental ``fees_paid`` update: ``COALESCE(fees_paid, 0) + fee_delta``.

    PK-only ``WHERE`` clause per H-018. T-218b body invokes once per
    execution event so multiple fills on the same trade accumulate
    fees correctly.

    SQL note (per L-008): ``COALESCE(fees_paid, 0)`` — bare ``0``
    implicit-casts to NUMERIC at the column boundary (column is
    ``NUMERIC(20,4)`` per migration 0005). Do NOT use ``Decimal '0'``
    (Python type name, not a PostgreSQL type identifier — would raise
    ``syntax error at or near "Decimal"`` at runtime).
    """
    await conn.execute(
        """
        UPDATE trades
        SET fees_paid = COALESCE(fees_paid, 0) + $1
        WHERE id = $2
        """,
        fee_delta,
        trade_id,
    )


@non_idempotent
async def insert_execution(
    conn: _DbExecutor,
    *,
    exchange_exec_id: str,
    order_id: int,
    trade_id: int | None,
    bot_id: str,
    symbol: str,
    side: Literal["buy", "sell"],
    price: Decimal,
    qty: Decimal,
    fee: Decimal,
    exec_type: str,
    executed_at: datetime,
) -> None:
    """INSERT ``executions`` row (hypertable; PK ``(executed_at, id)`` with id BIGSERIAL).

    ``trade_id`` nullable per migration 0005 line 194 (backfilled when
    trade row materialises). T-218b dispatcher path always knows
    ``trade_id`` via lookup; nullable signature kept for future T-221
    reconciliation paths that may insert orphan executions.

    ``exec_type`` is plain TEXT NOT NULL with NO CHECK constraint per
    migration 0005:202 — the §7.2:1027 enum (``'open' | 'partial_tp' |
    'sl' | 'trail' | 'close'``) is documentary, not enforced. T-218b
    OQ-1 default branch additionally writes ``'unknown'`` + WARN log
    when neither orders-lookup nor position_state-inference yields a
    match (operator-actionable signal). T-218b plan-reviewer to
    re-affirm the ``'unknown'`` admissibility OR escalate to ADR +
    future migration adding ``CHECK (exec_type IN (...))``.
    """
    await conn.execute(
        """
        INSERT INTO executions (
            exchange_exec_id, order_id, trade_id, bot_id, symbol, side,
            price, qty, fee, exec_type, executed_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        exchange_exec_id,
        order_id,
        trade_id,
        bot_id,
        symbol,
        side,
        price,
        qty,
        fee,
        exec_type,
        executed_at,
    )


# T-217a — PositionLifecycle FSM helpers (§9.5:1585-1592) -------------------


async def select_trade_fsm_params(
    conn: _DbExecutor,
    trade_id: int,
) -> dict[str, Decimal] | None:
    """Read FSM runtime params from ``trades.meta`` JSONB by ``trades.id`` PK.

    Returns ``{'be_trigger': Decimal(...), 'be_sl_level': Decimal(...),
    'trail_pct': Decimal(...)}`` or None if trade row missing. Pydantic
    Decimal-as-string serialization expected (per :func:`insert_trade`
    ``meta`` kwarg convention via ``json.dumps(default=str)``).
    """
    row = await conn.fetchrow(
        "SELECT meta FROM trades WHERE id = $1",
        trade_id,
    )
    if row is None:
        return None
    meta_raw = row["meta"]
    meta: dict[str, Any] = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
    return {
        "be_trigger": Decimal(meta["be_trigger"]),
        "be_sl_level": Decimal(meta["be_sl_level"]),
        "trail_pct": Decimal(meta["trail_pct"]),
    }


async def update_position_state_monitor_tick(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    best_price: Decimal,
    mfe_price: Decimal,
    mae_price: Decimal,
    running_pnl: Decimal,
    updated_at: datetime,
) -> None:
    """Composite-PK UPDATE for monitor-loop fields.

    H-018-symmetric: composite PK ``(bot_id, symbol)`` only; no LIKE-style
    multi-row updates possible. Distinct from
    :func:`update_position_state_after_fill` (which writes ``remaining_qty`` +
    ``sl_type``) — this writer owns monitor-only fields, no overlap with
    fill-flow writes (column-disjoint UPDATEs are MVCC-safe regardless of
    interleaving).
    """
    await conn.execute(
        """
        UPDATE position_state
        SET best_price = $1, mfe_price = $2, mae_price = $3,
            running_pnl = $4, updated_at = $5
        WHERE bot_id = $6 AND symbol = $7
        """,
        best_price,
        mfe_price,
        mae_price,
        running_pnl,
        updated_at,
        bot_id,
        symbol,
    )


# T-217b — PositionLifecycle BE trigger + trail SL adjustment ----------------


async def update_position_state_sl(
    conn: _DbExecutor,
    *,
    bot_id: str,
    symbol: str,
    sl_price: Decimal,
    sl_type: Literal["protective", "be", "trail"],
    updated_at: datetime,
) -> None:
    """Composite-PK UPDATE for SL-adjustment paths (T-217b BE / trail).

    H-018-symmetric: composite PK ``(bot_id, symbol)`` only. Column-disjoint
    from :func:`update_position_state_after_fill` (writes ``remaining_qty``)
    and :func:`update_position_state_monitor_tick` (writes monitor-only fields)
    — distinct writer for SL-adjustment flow keeps the multi-writer choreography
    column-disjoint and MVCC-safe.

    Write-side ``sl_type`` is tightened to ``Literal["protective","be","trail"]``
    so a typo at the call-site fails compile-time. Read-side
    :class:`PositionStateRow.sl_type` stays loose ``str | None`` for read
    robustness.
    """
    await conn.execute(
        """
        UPDATE position_state
        SET sl_price = $1, sl_type = $2, updated_at = $3
        WHERE bot_id = $4 AND symbol = $5
        """,
        sl_price,
        sl_type,
        updated_at,
        bot_id,
        symbol,
    )


# T-220a — P&L audit loop helpers (§9.5:1601-1605; H-017; ADR-0007 D7) -------


async def select_realized_pnl_sum_for_bots_since(
    conn: _DbExecutor,
    *,
    bot_ids: list[str],
    since: datetime,
) -> Decimal:
    """Sum trades.realized_pnl WHERE bot_id IN (...) AND closed_at >= since.

    T-220b audit uses this for cumulative_db computation per sub-account window.
    Multi-bot list supports cross-bot shared-sub-account composition (per ADR-0004).
    NULL realized_pnl filtered (trades not yet closed by T-219 reconcile_close)
    via ``closed_at IS NOT NULL`` predicate. Empty bot_ids → returns Decimal("0")
    without DB roundtrip (defensive against sub_account misconfiguration per WG#7).
    """
    if not bot_ids:
        return Decimal("0")
    row = await conn.fetchrow(
        """
        SELECT COALESCE(SUM(realized_pnl), 0) AS total
        FROM trades
        WHERE bot_id = ANY($1::text[])
          AND closed_at IS NOT NULL
          AND closed_at >= $2
        """,
        bot_ids,
        since,
    )
    if row is None:
        return Decimal("0")
    total = row["total"]
    if isinstance(total, Decimal):
        return total
    return Decimal(total)


@non_idempotent
async def insert_trade_pnl_delta(
    conn: _DbExecutor,
    *,
    sub_account: str,
    audit_run_at: datetime,
    window_start: datetime,
    window_end: datetime,
    cumulative_bybit: Decimal,
    cumulative_db: Decimal,
    delta: Decimal,
) -> None:
    """INSERT trade_pnl_deltas; UNIQUE (sub_account, audit_run_at) raises on dup.

    ``@non_idempotent`` per T-213b precedent — INSERT writes audit-grade row;
    no replay-safe key. ``UniqueViolationError`` surfaces concurrent-run conflict
    per ADR-0007 D7 (let DB raise; T-220b job catches + WARN + next tick).
    """
    await conn.execute(
        """
        INSERT INTO trade_pnl_deltas (
            sub_account, audit_run_at, window_start, window_end,
            cumulative_bybit, cumulative_db, delta
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        sub_account,
        audit_run_at,
        window_start,
        window_end,
        cumulative_bybit,
        cumulative_db,
        delta,
    )
