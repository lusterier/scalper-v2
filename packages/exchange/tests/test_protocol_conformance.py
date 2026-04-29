"""§11.6 adapter protocol conformance contract test.

Parametrised over every ExchangeClient implementation registered in
:data:`_ADAPTERS_UNDER_TEST`. Each adapter is asserted against the
brief minimum (§11.6 verbatim):

* every Protocol method is present on the adapter class.
* every Protocol method NOT in
  :data:`packages.exchange.protocols._UNLABELED_METHODS` carries
  ``@idempotent`` or ``@non_idempotent`` marker per §N3.

H-013 binding (per OQ-2 default A): every adapter's
:meth:`set_trading_stop` has ``tpsl_mode`` as the third positional
parameter (after ``self``, ``symbol``) with **no default value**.
The mypy strict + Protocol structural typing check catches default
drift at lint time, but T-206 contract test must hold even if mypy
is bypassed (e.g., a future adapter ``# type: ignore`` slipping past
review). Capital-loss-preventing invariant — pinned at adapter-pool
layer, not just per-adapter (T-211) or type-only (T-201).

T-206 is the first real reader of :data:`_UNLABELED_METHODS` per the
operator-locked rationale block in :mod:`packages.exchange.protocols`
(T-201 W#1).

Adapter registration is manual; an adapter class not appended to
:data:`_ADAPTERS_UNDER_TEST` is silently skipped — per-adapter test
files (mirror T-211 ``test_adapter.py``) are the per-class safety
net. T-207 (BybitV5Adapter) appends its class to the list at ship
time via 1-line edit.
"""

from __future__ import annotations

import inspect

import pytest

from packages.core import is_idempotent, is_non_idempotent
from packages.exchange import ExchangeClient, PaperExchange
from packages.exchange.bybit_v5 import BybitV5Adapter
from packages.exchange.protocols import _UNLABELED_METHODS

# Adapter registry. Future ExchangeClient implementations append here.
# T-208a appended BybitV5Adapter per T-206 forward-extensibility note.
# Underscore-prefixed = test-internal, not re-exported.
_ADAPTERS_UNDER_TEST: list[type] = [PaperExchange, BybitV5Adapter]


def _protocol_method_names() -> set[str]:
    """Public callable names declared on the ExchangeClient Protocol."""
    return {
        name
        for name, member in inspect.getmembers(ExchangeClient)
        if callable(member) and not name.startswith("_")
    }


@pytest.mark.parametrize("adapter_cls", _ADAPTERS_UNDER_TEST)
def test_adapter_implements_every_protocol_method(adapter_cls: type) -> None:
    """§11.6: every adapter implements every ExchangeClient method."""
    expected = _protocol_method_names()
    actual = {
        name
        for name, member in inspect.getmembers(adapter_cls)
        if callable(member) and not name.startswith("_")
    }
    missing = expected - actual
    assert not missing, f"{adapter_cls.__name__} missing ExchangeClient methods: {missing}"


@pytest.mark.parametrize("adapter_cls", _ADAPTERS_UNDER_TEST)
def test_adapter_methods_carry_correct_idempotency_markers(
    adapter_cls: type,
) -> None:
    """§N3 + T-201 W#1: every Protocol method NOT in _UNLABELED_METHODS
    carries @idempotent or @non_idempotent on every adapter.

    Streams + close are exempt per the operator-locked rationale block
    in ``packages/exchange/protocols.py`` (T-201 W#1). Markers are keyed
    by ``f"{func.__module__}.{func.__qualname__}"`` per
    ``packages/core/markers.py``, so ``getattr(adapter_cls, name)``
    correctly resolves the adapter's own method registration (NOT the
    Protocol's); do not simplify by walking ``ExchangeClient.method``.
    """
    unlabeled: list[str] = []
    for name in _protocol_method_names():
        if name in _UNLABELED_METHODS:
            continue
        method = getattr(adapter_cls, name)
        if not (is_idempotent(method) or is_non_idempotent(method)):
            unlabeled.append(name)
    assert not unlabeled, f"{adapter_cls.__name__} has unlabeled non-exempt methods: {unlabeled}"


@pytest.mark.parametrize("adapter_cls", _ADAPTERS_UNDER_TEST)
def test_adapter_unlabeled_methods_carry_no_markers(adapter_cls: type) -> None:
    """T-201 W#1 negative: streams + close MUST NOT carry markers.

    A future implementer accidentally decorating ``stream_executions``
    with ``@idempotent`` would silently change the §5.8 marker
    invariant; this test catches that regression.
    """
    for name in _UNLABELED_METHODS:
        method = getattr(adapter_cls, name)
        assert not is_idempotent(method), (
            f"{adapter_cls.__name__}.{name} is in _UNLABELED_METHODS but is decorated @idempotent"
        )
        assert not is_non_idempotent(method), (
            f"{adapter_cls.__name__}.{name} is in _UNLABELED_METHODS "
            f"but is decorated @non_idempotent"
        )


@pytest.mark.parametrize("adapter_cls", _ADAPTERS_UNDER_TEST)
def test_adapter_set_trading_stop_tpsl_mode_has_no_default_and_is_third_positional(
    adapter_cls: type,
) -> None:
    """H-013 binding per OQ-2 default A: ``tpsl_mode`` no-default invariant
    enforced on every adapter at the contract-test layer.

    Pins the full T-201 W#2 contract: ``tpsl_mode`` is the **third**
    parameter (after ``self``, ``symbol``) with NO default value. mypy
    strict + Protocol structural typing catches default drift at lint
    time, but the contract test must hold even if mypy is bypassed
    (e.g., a future adapter ``# type: ignore`` slipping past review).
    """
    set_trading_stop = adapter_cls.set_trading_stop  # type: ignore[attr-defined]
    sig = inspect.signature(set_trading_stop)
    params = list(sig.parameters)
    assert params[0] == "self", (
        f"{adapter_cls.__name__}.set_trading_stop first param must be self, got {params[0]!r}"
    )
    assert params[1] == "symbol", (
        f"{adapter_cls.__name__}.set_trading_stop second param must be symbol, got {params[1]!r}"
    )
    assert params[2] == "tpsl_mode", (
        f"{adapter_cls.__name__}.set_trading_stop tpsl_mode must be the third "
        f"positional param (after self, symbol) per H-013 / T-201 W#2; "
        f"got {params[2]!r}"
    )
    assert sig.parameters["tpsl_mode"].default is inspect.Parameter.empty, (
        f"{adapter_cls.__name__}.set_trading_stop tpsl_mode must have no default "
        f"(H-013); got default={sig.parameters['tpsl_mode'].default!r}"
    )


def test_unlabeled_methods_set_is_complete() -> None:
    """Sanity: ``_UNLABELED_METHODS`` is the expected 3-element subset of
    ExchangeClient Protocol methods.

    Pinned counts:

    * ExchangeClient declares exactly 10 public methods (§11.1 verbatim:
      4 writes + 3 reads + 2 streams + 1 lifecycle). Catches future
      Python upstream additions to ``typing.Protocol`` that might
      silently expand the surface and pass the contract test vacuously.
    * ``_UNLABELED_METHODS`` has exactly 3 entries (``stream_executions``,
      ``stream_positions``, ``close``). Forces a deliberate update path:
      if a future task adds a fourth exemption, both the frozenset and
      this assertion change in the same diff, surfacing the decision
      for review.
    """
    protocol_methods = _protocol_method_names()
    assert len(protocol_methods) == 10, (
        f"ExchangeClient must declare exactly 10 public methods (§11.1 verbatim); "
        f"got {len(protocol_methods)}: {sorted(protocol_methods)}"
    )
    bad = _UNLABELED_METHODS - protocol_methods
    assert not bad, f"_UNLABELED_METHODS contains names not in ExchangeClient Protocol: {bad}"
    assert _UNLABELED_METHODS, "_UNLABELED_METHODS must be non-empty"
    assert len(_UNLABELED_METHODS) == 3, (
        f"_UNLABELED_METHODS must have exactly 3 entries "
        f"(stream_executions, stream_positions, close); "
        f"got {len(_UNLABELED_METHODS)}: {sorted(_UNLABELED_METHODS)}"
    )
