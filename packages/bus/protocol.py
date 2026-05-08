"""Bus interface contract — Python ``typing.Protocol``.

Declared once; implementations: :class:`packages.bus.NatsClient` (live
NATS JetStream) + :class:`packages.bus.ReplayBus` (in-process timestamp-
ordered replay, T-502).

T-507a — introduced so strategy-engine + execution-service + paper-engine
+ scoring-resolver consumer signatures can accept either implementation
without per-call-site ``cast`` / ``# type: ignore``. Mypy strict-clean.

Per BRIEF §12.2:1958 *"strategy-engine and execution-service: reused
unchanged; wired to ReplayBus instead of live NATS"*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from packages.bus.client import Handler
    from packages.bus.envelope import MessageEnvelope


__all__ = ["BusProtocol"]


class BusProtocol(Protocol):
    """Structural type for bus implementations.

    Implementations:

    * :class:`packages.bus.NatsClient` — production / live NATS JetStream
      (`packages/bus/client.py:78`).
    * :class:`packages.bus.ReplayBus` — in-process timestamp-ordered
      replay (`packages/bus/replay_bus.py:93`; T-502).

    ``subscribe`` returns ``object`` because NatsClient returns
    ``nats.aio.subscription.Subscription`` and ReplayBus returns
    ``ReplaySubscription``; no current consumer reads the returned value
    (3 call-sites verified at T-507a plan-time:
    ``services/strategy_engine/app/main.py:139``,
    ``services/execution/app/main.py:176``,
    ``packages/exchange/paper/adapter.py:249`` — all
    ``await bus.subscribe(...)`` discarding the return).

    KV methods: NatsClient implements via JetStream KV; ReplayBus stubs
    return ``None`` for ``kv_get`` and raise ``NotImplementedError`` for
    ``kv_put``/``kv_update`` (replay has no KV write semantic).
    :class:`packages.scoring.FeatureResolver` ``_try_kv`` handles ``None``
    via existing fallback to ``_try_db``
    (`packages/scoring/resolver.py:181-182`).

    Note: ``@runtime_checkable`` decorator deliberately omitted per §0.8 —
    static typing is sufficient; runtime ``isinstance`` overhead avoided.
    Protocol satisfaction at runtime is verified by ``inspect.getmembers``
    introspection in ``packages/bus/tests/test_bus_protocol.py``.
    """

    async def publish(self, subject: str, envelope: MessageEnvelope) -> None: ...

    async def subscribe(self, subject: str, handler: Handler) -> object: ...

    async def close(self) -> None: ...

    async def kv_get(self, bucket: str, key: str) -> tuple[bytes, int] | None: ...

    async def kv_put(self, bucket: str, key: str, value: bytes) -> int: ...

    async def kv_update(self, bucket: str, key: str, value: bytes, last_revision: int) -> int: ...
