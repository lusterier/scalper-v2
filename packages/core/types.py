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
    "Symbol",
    "TraceId",
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
