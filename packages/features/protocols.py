"""The :class:`Feature` Protocol — port that built-in indicators
(T-107) and plugins implement.

Per §5.3 ("Protocol for ports") the contract is structural and
mypy-time only. ``@runtime_checkable`` is intentionally **not**
applied — :func:`isinstance` against a runtime-checkable Protocol
verifies methods but not data attributes, so ``isinstance(obj,
Feature)`` would silently accept implementations missing
``name_template``/``interval``/``warmup_candles``. T-111 plugin
discovery does explicit attribute introspection where it needs
runtime checks; the typing contract here remains a clean port
without misleading runtime semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .types import FeatureValue, OhlcCandle

__all__ = ["Feature"]


class Feature(Protocol):
    """§9.3 Feature port — pure compute over a candle window.

    ``name_template`` carries placeholder tokens (``{symbol}``,
    ``{interval}``, plus indicator-specific tokens like ``{period}``)
    that the registry (T-111) substitutes at registration time to
    yield the concrete published feature name (e.g.,
    ``ind.btcusdt.15m.ema_20``).

    ``source_version`` pins the output against the algorithm version;
    a behaviour change requires a fresh suffix and triggers backfill
    via T-112 ``backfill_features.py``. Two implementations sharing
    a name template but disagreeing on values would otherwise
    contaminate the ``features`` table silently.

    ``interval`` is the candle interval the feature consumes
    (``"15m"``, ``"1h"``, ...). ``warmup_candles`` is the minimum
    history length :meth:`compute` needs to produce a defined value
    — the engine queries the last ``warmup_candles + k`` rows from
    the matching continuous aggregate at startup and feeds them in.

    :meth:`compute` is **sync** because it is pure CPU over a
    bounded window — no I/O. The engine wraps the call in its own
    async event loop; running CPU-bound work synchronously is
    correct per §5.5 (async only for I/O).
    """

    name_template: str
    source_version: str
    interval: str
    warmup_candles: int

    def compute(self, candles: Sequence[OhlcCandle]) -> FeatureValue: ...
