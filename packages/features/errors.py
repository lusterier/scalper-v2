"""Exception hierarchy for :mod:`packages.features` (§5.4).

All errors raised by this package inherit from :class:`FeaturesError`,
which itself inherits from :class:`packages.core.ScalperError`, so
callers can narrow to ``except FeaturesError`` or broaden to
``except ScalperError`` without depending on indicator-specific modules.
"""

from __future__ import annotations

from packages.core import ScalperError

__all__ = ["FeatureUnderflowError", "FeaturesError"]


class FeaturesError(ScalperError):
    """Base class for errors raised by :mod:`packages.features`."""


class FeatureUnderflowError(FeaturesError, ValueError):
    """Raised by :meth:`Feature.compute` when ``len(candles) < warmup_candles``.

    The engine MUST respect the warmup contract; a violation indicates
    an upstream bug (warmup buffer not primed, off-by-one in the
    engine, continuous aggregate returning fewer rows than expected).
    Failing loud at the indicator boundary surfaces such bugs at the
    earliest reachable site rather than letting them silently distort
    a downstream feature value.

    Inherits from both :class:`FeaturesError` and :class:`ValueError`:

    - **Primary contract:** ``except FeaturesError`` (project hierarchy).
      §5.4 deprecates raising bare ``ValueError`` for domain errors;
      the typed family is the primary catch.
    - **Convenience surface:** ``except ValueError`` also matches, so
      callers that already expect ``ValueError`` for argument-shape
      violations need not change. This is *additional* surface, not
      the contract — new callers should use :class:`FeaturesError`.
    """
