"""Intra-candle deterministic path generator (T-505 / brief §12.2:1961-1963).

Pure function. No I/O, no globals, no side effects. Output drives F5
PaperExchange replay-mode SL/TP cross detection (T-506) at intra-candle
granularity, replacing T-213a's Q4-A pessimistic SL-first simplification
with the full TradingView "Replay" algorithm.

Algorithm verbatim from BRIEF §12.2:1961-1963: each 1-minute candle
generates a deterministic 4-point path ``O → first_extreme → second_extreme
→ C`` where ``first_extreme = high if close > open else low`` (toward-high
direction for bullish candles, toward-low for bearish).

Doji handling: BRIEF §12.2:1961 strictly says ``close > open``; this module
extends to ``close >= open`` (doji uniformly toward high) for total-
functionality coverage and to avoid undefined-behavior on the equal-price
branch. See T-505 plan-doc OQ-2=A.

Defensive OHLC validation (per T-505 OQ-3=A): raises ``ValueError`` on
``low > high`` or ``open``/``close`` outside ``[low, high]``. ``§0.8``
anti-hypothetical caveat: this function is NOT a system boundary — its
upstream caller is T-506 PaperExchange consuming T-503 HistoricalOHLCSource
which reads schema-validated ``ohlc_1m`` rows. Validation is belt-and-
suspenders for early F5 development; T-519 hazard audit may reassess and
remove if redundant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

__all__ = ["generate_intra_candle_path"]


def generate_intra_candle_path(
    *,
    open: Decimal,  # noqa: A002 — BRIEF §12.2:1961 nomenclature; keyword-only avoids call-site ambiguity
    high: Decimal,
    low: Decimal,
    close: Decimal,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return deterministic 4-point intra-candle price path per §12.2:1961-1963.

    Path: ``(open, first_extreme, second_extreme, close)`` where
    ``first_extreme = high if close >= open else low``. The doji case
    (``close == open``) extends BRIEF's strict ``close > open`` rule to
    ``close >= open`` — see module docstring + T-505 plan OQ-2=A.

    Three segments ``(open → first_extreme), (first_extreme → second_extreme),
    (second_extreme → close)`` define a piecewise-linear price path that
    visits BOTH extremes within the candle, matching TradingView "Replay"
    semantics for SL/TP cross detection.

    Decimal preserved end-to-end per §5.3 (NUMERIC ohlc_1m source).

    Raises ``ValueError`` on malformed OHLC: ``low > high`` or ``open`` /
    ``close`` outside ``[low, high]``. Defensive per OQ-3=A; see module
    docstring §0.8 caveat.
    """
    if low > high:
        msg = f"intra_candle: low > high (low={low!r}, high={high!r})"
        raise ValueError(msg)
    if open < low or open > high:
        msg = f"intra_candle: open outside [low, high] (open={open!r}, low={low!r}, high={high!r})"
        raise ValueError(msg)
    if close < low or close > high:
        msg = (
            f"intra_candle: close outside [low, high] (close={close!r}, low={low!r}, high={high!r})"
        )
        raise ValueError(msg)

    if close >= open:  # bullish or doji (per OQ-2=A)
        return (open, high, low, close)
    return (open, low, high, close)
