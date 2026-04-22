"""Concrete NATS payload schemas (§8.4).

Each payload type lives with the service that owns it and is added to
this namespace as the owning task lands:

* :class:`SignalValidated` — owned by signal-gateway, landed T-015b1.
* ``OrderRequest`` / ``OrderEvent*`` — F2 execution.
* ``OHLCCandle`` — F1 market-data.
* ``FeatureUpdate`` — F1 feature-engine.
"""

from __future__ import annotations

from .signals import SignalValidated, message_id_for

__all__ = ["SignalValidated", "message_id_for"]
