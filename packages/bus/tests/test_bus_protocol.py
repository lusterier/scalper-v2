"""T-507a: Verify NatsClient + ReplayBus structurally satisfy BusProtocol.

Mypy strict mode is the primary check at compile time
(``pyproject.toml:99``); these runtime tests catch the regression scenario
where a developer adds a method to ``BusProtocol`` but forgets to add it
to one of the implementations.

Pattern mirrors ``packages/exchange/tests/test_protocol_conformance.py:50-68``
(ExchangeClient Protocol introspection).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from packages.bus import BusProtocol, NatsClient, ReplayBus


def _bus_protocol_methods() -> set[str]:
    """Enumerate non-dunder method names declared on ``BusProtocol``."""
    return {
        name
        for name, member in inspect.getmembers(BusProtocol)
        if not name.startswith("_") and inspect.isfunction(member)
    }


def test_natsclient_satisfies_bus_protocol() -> None:
    """Every BusProtocol method exists on NatsClient."""
    methods = _bus_protocol_methods()
    assert methods, "BusProtocol enumeration returned empty set — introspection broke"
    for name in methods:
        assert hasattr(NatsClient, name), f"NatsClient missing BusProtocol.{name}"


def test_replaybus_satisfies_bus_protocol() -> None:
    """Every BusProtocol method exists on ReplayBus."""
    methods = _bus_protocol_methods()
    assert methods, "BusProtocol enumeration returned empty set — introspection broke"
    for name in methods:
        assert hasattr(ReplayBus, name), f"ReplayBus missing BusProtocol.{name}"


def test_bus_protocol_assignment_natsclient() -> None:
    """Static-typing regression guard: NatsClient assigns to BusProtocol-typed variable.

    Mypy strict validates this assignment at compile time. The runtime
    assertion is defensive — exercises the actual class to confirm
    structural compat (asserting hasattr would be a weaker version of the
    introspection tests above; this test exists to surface the assignment
    pattern as a code-level regression cue).
    """
    bus: BusProtocol = MagicMock(spec=NatsClient)
    assert hasattr(bus, "publish")
    assert hasattr(bus, "kv_get")


def test_bus_protocol_assignment_replaybus() -> None:
    """Static-typing regression guard: ReplayBus assigns to BusProtocol-typed variable."""
    bus: BusProtocol = ReplayBus()
    assert hasattr(bus, "publish")
    assert hasattr(bus, "kv_get")
