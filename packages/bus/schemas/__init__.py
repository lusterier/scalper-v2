"""Concrete NATS payload schemas (§8.4).

Each payload type lives with the service that owns it and is added to
this namespace as the owning task lands:

* :class:`SignalValidated` — owned by signal-gateway, landed T-015b1.
* ``OrderRequest`` / ``OrderEvent*`` — F2 execution.
* :class:`OhlcCandlePayload` — owned by market-data-svc, landed T-104.
* ``FeatureUpdate`` — F1 feature-engine.
"""

from __future__ import annotations

from .feature_update import FeatureUpdate, subject_for
from .market_ohlc import OhlcCandlePayload, message_id_for_closed_candle
from .signals import SignalValidated, message_id_for

__all__ = [
    "FeatureUpdate",
    "OhlcCandlePayload",
    "SignalValidated",
    "message_id_for",
    "message_id_for_closed_candle",
    "subject_for",
]
