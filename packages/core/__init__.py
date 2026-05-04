"""Shared domain types, errors, idempotency markers, and UTC time helper.

`packages/core` has no internal dependencies (§4). Re-exports the public
API; test-only helpers in `core.time` (`_testing_*`) are intentionally
NOT re-exported here.
"""

from __future__ import annotations

from .errors import ScalperError
from .markers import (
    UnlabeledMethodError,
    assert_all_methods_labeled,
    idempotent,
    is_idempotent,
    is_non_idempotent,
    non_idempotent,
)
from .time import now_utc
from .types import (
    Action,
    BacktestStatus,
    BotId,
    BotStatus,
    CorrelationId,
    ExchangeMode,
    ExchangeSource,
    IngestionStatus,
    ScoringDecision,
    Symbol,
    TraceId,
    TradeStatus,
)

__all__ = [
    "Action",
    "BacktestStatus",
    "BotId",
    "BotStatus",
    "CorrelationId",
    "ExchangeMode",
    "ExchangeSource",
    "IngestionStatus",
    "ScalperError",
    "ScoringDecision",
    "Symbol",
    "TraceId",
    "TradeStatus",
    "UnlabeledMethodError",
    "assert_all_methods_labeled",
    "idempotent",
    "is_idempotent",
    "is_non_idempotent",
    "non_idempotent",
    "now_utc",
]
