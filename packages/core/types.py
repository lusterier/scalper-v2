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
    "BotId",
    "BotStatus",
    "CorrelationId",
    "ExchangeMode",
    "ExchangeSource",
    "Symbol",
    "TraceId",
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
