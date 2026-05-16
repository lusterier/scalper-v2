"""Domain primitives shared across services.

NewType aliases for IDs and three closed enums covering the F0 surface.
Future domain types live with their owners (e.g., `Side` and `OrderType`
in `packages/exchange`); this module stays small.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NewType

__all__ = [
    "Action",
    "BacktestStatus",
    "BotId",
    "BotStatus",
    "CorrelationId",
    "ExchangeMode",
    "ExchangeSource",
    "IngestionStatus",
    "ScoringDecision",
    "ShadowRejectedTerminal",
    "ShadowVariantTerminal",
    "Symbol",
    "TraceId",
    "TradeLifecycleState",
    "TradeStatus",
]


BotId = NewType("BotId", str)
Symbol = NewType("Symbol", str)
CorrelationId = NewType("CorrelationId", str)
TraceId = NewType("TraceId", str)


class Action(StrEnum):
    """Inbound signal action (§7.2 signals DDL).

    `CUSTOM` is the domain-level escape hatch for non-trade signal kinds;
    wire schemas (§8.4) may narrow to a stricter subset.
    """

    LONG = "LONG"
    SHORT = "SHORT"
    CLOSE = "CLOSE"
    CUSTOM = "CUSTOM"


class BacktestStatus(StrEnum):
    """backtest_runs.status enum (BRIEF §7.2:1151).

    F4 (T-407): only ``QUEUED`` is ever written by the trigger endpoint.
    F5+ worker transitions ``QUEUED → RUNNING → (COMPLETED|FAILED)``.

    All four values are defined here so the row-narrowing path
    (``BacktestStatus(value)`` in :func:`_row_to_backtest_run`) does NOT
    raise on rows written by a future F5+ worker. Forward-compat is the
    explicit reason every value lives in F4 enum even when only one value
    is ever written today.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BotStatus(StrEnum):
    """Bot registry status (§7.2 bots DDL)."""

    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class ShadowVariantTerminal(StrEnum):
    """shadow_variants.terminal_outcome enum (BRIEF §13.3).

    F5 (T-511 shadow-worker FSM) writes one of these 5 values when a
    parallel-simulation variant terminates. Mirror BacktestStatus
    forward-compat pattern — all values defined at row-narrowing site so
    future ``replay-error`` / ``shutdown-mid-replay`` (T-512 restart
    recovery edge cases) only need a value addition here, no DB
    migration (T-510a OQ-4=A: terminal_outcome stored as plain TEXT
    with no CHECK constraint).
    """

    SL_HIT = "sl_hit"
    BE_HIT = "be_hit"
    TP_TRAIL = "tp_trail"
    TP_FULL = "tp_full"
    TIMEOUT = "timeout"
    # T-512a / OQ-4=A: parent trade closed during downtime → shadow variant
    # cannot resume meaningfully (parent context gone). NOT a "lost_on_restart"
    # state per BRIEF §20:2785 H-023 policy — variant IS recorded with explicit
    # terminal outcome. App-layer extension only (column TEXT no CHECK per
    # T-510a OQ-4=A).
    SHUTDOWN_MID_REPLAY = "shutdown_mid_replay"


class ShadowRejectedTerminal(StrEnum):
    """shadow_rejected.terminal_outcome enum (BRIEF §13.5).

    F5 (T-513 rejected-signal observation) writes one of these values
    when the 60-min observation window closes. Mirror
    :class:`ShadowVariantTerminal` forward-compat pattern (column TEXT no
    CHECK per T-510a OQ-4=A — value additions are app-layer only, no
    migration).
    """

    WOULD_TP = "would_tp"
    WOULD_SL = "would_sl"
    WOULD_BE = "would_be"
    NO_TRIGGER = "no_trigger"
    # T-513b1 / OQ-2 baked: replay-recovery edge cases (window cap exceeded
    # OR per-task compute timeout) → observation cannot resume meaningfully.
    # Mirror :data:`ShadowVariantTerminal.SHUTDOWN_MID_REPLAY` semantic per
    # T-512a OQ-4=A precedent. Distinguishes "couldn't observe long enough"
    # from "observed but no trigger" — operator-visible classification.
    SHUTDOWN_MID_REPLAY = "shutdown_mid_replay"


class ExchangeMode(StrEnum):
    """Exchange mode the bot operates against (§7.2 bots DDL)."""

    LIVE = "live"
    TESTNET = "testnet"
    PAPER = "paper"


class ExchangeSource(StrEnum):
    """Origin of a symbol_map alias (§7.2 symbol_map DDL).

    `binance` for TradingView-side aliases like `BTCUSDT.P`; `bybit` for
    Bybit-canonical inputs; `custom` for operator-defined entries.
    """

    BINANCE = "binance"
    BYBIT = "bybit"
    CUSTOM = "custom"


class TradeStatus(StrEnum):
    """Trade lifecycle status (§7.2 trades DDL line 1003).

    `open` while position is in-flight; `closed` after T-219 reconcile
    finalises realized_pnl; `error` for orphan / partial-failure trades
    flagged by T-221 reconciliation.
    """

    OPEN = "open"
    CLOSED = "closed"
    ERROR = "error"


class TradeLifecycleState(StrEnum):
    """Canonical named trade-lifecycle FSM state (T-533 / ADR-0011).

    Single observable state consolidating the legacy 4-column model
    (``trades.status`` / ``trades.close_reason`` /
    ``position_state.{tp_hit,sl_type,trailing_active}``). **Additive /
    observability-only (T-533 OQ-1=A)** — the legacy columns remain
    authoritative for all decision logic; nothing reads this. Stored as
    plain ``TEXT`` (``trades.lifecycle_state``), no DB CHECK — value
    additions are app-layer only, no migration (mirror
    :class:`ShadowVariantTerminal` forward-compat pattern).

    Backfill steady-state subset (migration 0020, from the legacy 4
    columns): ``OPEN`` / ``PARTIALLY_CLOSED`` / ``BREAKEVEN_SET`` /
    ``TRAILING_ACTIVE`` / ``CLOSED`` / ``RECONCILED`` / ``ORPHANED`` +
    ``FAILED`` (from ``trades.status='error'`` — defensive /
    enum-vocab-complete; no current writer, the T-221 orphan/partial-
    failure intent). Transient states ``SIGNAL_RECEIVED`` /
    ``ORDER_REQUESTED`` / ``ORDER_PLACED`` / ``CLOSING`` / ``TP_HIT`` are
    **forward-only** — no legacy column records them; reachable only via
    T-533b dual-write going forward.
    """

    SIGNAL_RECEIVED = "signal_received"
    ORDER_REQUESTED = "order_requested"
    ORDER_PLACED = "order_placed"
    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"
    TP_HIT = "tp_hit"
    BREAKEVEN_SET = "breakeven_set"
    TRAILING_ACTIVE = "trailing_active"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"
    ORPHANED = "orphaned"
    RECONCILED = "reconciled"


class IngestionStatus(StrEnum):
    """signals.ingestion_status enum (§7.2:890).

    `validated` for clean inbound signals; `duplicate` for idempotency-key
    repeats; `invalid` for schema/HMAC failures (signal-gateway records
    them anyway for audit per §9.1).
    """

    VALIDATED = "validated"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


class ScoringDecision(StrEnum):
    """scoring_evaluations.decision enum (§7.2:1046).

    `execute` → strategy-engine emits OrderRequest; `reject` → discarded
    with audit row; `passthrough` → bot in shadow/observe mode emits
    no order but records the would-be evaluation.
    """

    EXECUTE = "execute"
    REJECT = "reject"
    PASSTHROUGH = "passthrough"
