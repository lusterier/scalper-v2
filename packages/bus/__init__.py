"""NATS JetStream bus primitives (§8, §3.3).

Public surface:

* :class:`MessageEnvelope` — §8.3 envelope shared by every publish.
* :class:`NatsClient` — async wrapper around ``nats-py`` (§8, §5.7),
  with JetStream publish + core-NATS ephemeral subscribe.
* :class:`ConnectionState` — lifecycle enum exposed for readiness
  probes and integration tests.
* Error hierarchy rooted at :class:`BusError` (itself a
  :class:`~packages.core.ScalperError`).

Concrete payload schemas under :mod:`packages.bus.schemas` land with
their owning services.
"""

from __future__ import annotations

from .client import ConnectionState, NatsClient
from .dedup import DedupingConsumer
from .envelope import MessageEnvelope
from .errors import BusError, NotConnectedError, PublishError, SubscribeError
from .protocol import BusProtocol
from .replay_bus import ReplayBus, ReplaySubscription

__all__ = [
    "BusError",
    "BusProtocol",
    "ConnectionState",
    "DedupingConsumer",
    "MessageEnvelope",
    "NatsClient",
    "NotConnectedError",
    "PublishError",
    "ReplayBus",
    "ReplaySubscription",
    "SubscribeError",
]
