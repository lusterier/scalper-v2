"""Concrete NATS payload schemas (§8.4).

Each payload type lives with the service that owns it and is added to
this namespace as the owning task lands (SignalValidated with T-015,
OrderRequest/OrderEvent* with F2 execution, OHLCCandle with F1
market-data, FeatureUpdate with F1 feature-engine).

T-008a ships the namespace empty so the package structure is in
place before the first owner arrives.
"""

from __future__ import annotations

__all__: list[str] = []
