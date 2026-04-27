"""ExchangeClient protocol, error taxonomy, and domain types (§11).

Public API for the F2 execution layer. :class:`ExchangeClient` is the
port that BybitV5Adapter (T-207..T-208) and PaperExchange (T-211..T-213)
implement. Domain return types (:class:`OrderPlaceResult`,
:class:`Position`, :class:`ExecutionEvent`, :class:`PositionEvent`) are
Python-internal frozen dataclasses, distinct from the §8.4 NATS wire
schemas; the seam is mapped at publish time in T-216b / T-218.

The error taxonomy (:class:`ExchangeError` and 5 subclasses) drives the
upper-layer decision matrix (retry vs. abort vs. reconcile per §11.3).
"""

from __future__ import annotations

from .errors import (
    AuthError,
    ExchangeError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
    UnknownState,
)
from .paper import PaperExchange, SlippageModel
from .protocols import ExchangeClient
from .types import ExecutionEvent, OrderPlaceResult, Position, PositionEvent

__all__ = [
    "AuthError",
    "ExchangeClient",
    "ExchangeError",
    "ExecutionEvent",
    "NetworkTimeout",
    "OrderPlaceResult",
    "OrderRejected",
    "PaperExchange",
    "Position",
    "PositionEvent",
    "RateLimitError",
    "SlippageModel",
    "UnknownState",
]
