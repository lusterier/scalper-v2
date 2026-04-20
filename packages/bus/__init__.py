"""NATS JetStream bus primitives (§8, §3.3).

T-008a ships the envelope contract (:class:`MessageEnvelope`, §8.3)
and error taxonomy (:class:`BusError`, :class:`NotConnectedError`).
The `NatsClient` wrapper with ``connect``/``publish``/``subscribe``
and the concrete payload schemas under :mod:`packages.bus.schemas`
land in subsequent tasks (T-008b and owner-service tasks respectively).
"""

from __future__ import annotations

from .envelope import MessageEnvelope
from .errors import BusError, NotConnectedError

__all__ = [
    "BusError",
    "MessageEnvelope",
    "NotConnectedError",
]
