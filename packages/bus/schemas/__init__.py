"""Concrete NATS payload schemas (§8.4).

Each payload type lives with the service that owns it and is added to
this namespace as the owning task lands:

* :class:`SignalValidated` — owned by signal-gateway, landed T-015b1.
* :class:`OrderRequest` / :class:`OrderPlaced` / :class:`OrderFilled` /
  :class:`OrderClosed` / :class:`SLMoved` / :class:`TradingEvent` — owned by
  execution-service (T-216a schemas; T-216b emission).
* :class:`OhlcCandlePayload` — owned by market-data-svc, landed T-104.
* ``FeatureUpdate`` — F1 feature-engine.
"""

from __future__ import annotations

from .feature_update import FeatureUpdate, subject_for
from .market_ohlc import OhlcCandlePayload, message_id_for_closed_candle
from .orders import (
    OrderClosed,
    OrderEventBase,
    OrderFilled,
    OrderPlaced,
    OrderRequest,
    SLMoved,
    TradingEvent,
    subject_for_orders_dlq,
    subject_for_orders_event,
    subject_for_orders_request,
)
from .signals import (
    SignalRejected,
    SignalValidated,
    message_id_for,
    subject_for_signals_rejected,
)

__all__ = [
    "FeatureUpdate",
    "OhlcCandlePayload",
    "OrderClosed",
    "OrderEventBase",
    "OrderFilled",
    "OrderPlaced",
    "OrderRequest",
    "SLMoved",
    "SignalRejected",
    "SignalValidated",
    "TradingEvent",
    "message_id_for",
    "message_id_for_closed_candle",
    "subject_for",
    "subject_for_orders_dlq",
    "subject_for_orders_event",
    "subject_for_orders_request",
    "subject_for_signals_rejected",
]
