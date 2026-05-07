"""§N4 unit tests for :mod:`packages.exchange.paper.intra_candle` (T-505).

Hand-computed fixtures match T-505 plan-doc §A-§H verification examples.
No implementation-against-itself; no library round-trip. Per-test
docstring cross-references the §-tag in plan-doc.

8 tests covering:

* Bullish candle (§A) — toward-high-first ordering.
* Bearish candle (§B) — toward-low-first ordering.
* Doji (§C) — bullish branch per OQ-2=A.
* Constant candle (§D) — all 4 prices equal.
* Open at low extreme (§E) — degenerate-but-correct.
* Decimal precision preservation (§F) — 12 fractional digits + ``isinstance``
  on ALL 4 outputs per WG#4.
* Validation: low > high (§G) — raises ``ValueError``.
* Validation: open/close outside [low, high] (§H) — parametrized.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from packages.exchange.paper.intra_candle import generate_intra_candle_path


def test_generate_intra_candle_path_bullish_visits_high_first() -> None:
    """§A — bullish (close > open): path = (open, high, low, close)."""
    path = generate_intra_candle_path(
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("95"),
        close=Decimal("105"),
    )
    assert path == (Decimal("100"), Decimal("110"), Decimal("95"), Decimal("105"))


def test_generate_intra_candle_path_bearish_visits_low_first() -> None:
    """§B — bearish (close < open): path = (open, low, high, close)."""
    path = generate_intra_candle_path(
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("95"),
        close=Decimal("97"),
    )
    assert path == (Decimal("100"), Decimal("95"), Decimal("110"), Decimal("97"))


def test_generate_intra_candle_path_doji_behaves_as_bullish() -> None:
    """§C — doji (close == open) per OQ-2=A: toward high first uniformly."""
    path = generate_intra_candle_path(
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("95"),
        close=Decimal("100"),
    )
    assert path == (Decimal("100"), Decimal("110"), Decimal("95"), Decimal("100"))


def test_generate_intra_candle_path_constant_candle_returns_all_equal() -> None:
    """§D — constant candle (open == high == low == close): all 4 prices equal."""
    path = generate_intra_candle_path(
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
    )
    assert path == (Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"))


def test_generate_intra_candle_path_open_at_low_extreme() -> None:
    """§E — open == low (bullish): degenerate-but-correct path."""
    path = generate_intra_candle_path(
        open=Decimal("95"),
        high=Decimal("110"),
        low=Decimal("95"),
        close=Decimal("105"),
    )
    # second_extreme = low = 95 = open; intermediate segment 110 → 95 returns to open price.
    assert path == (Decimal("95"), Decimal("110"), Decimal("95"), Decimal("105"))


def test_generate_intra_candle_path_preserves_decimal_precision() -> None:
    """§F — 12-fractional-digit Decimal preserved on ALL 4 outputs (§5.3 + WG#4)."""
    o = Decimal("65000.123456789012")
    h = Decimal("65010.987654321098")
    lo = Decimal("64990.111111111111")
    c = Decimal("65005.555555555555")
    path = generate_intra_candle_path(open=o, high=h, low=lo, close=c)
    # Bullish branch (c > o by 5.43...) → (o, h, lo, c) verbatim.
    assert path == (o, h, lo, c)
    # WG#4 lock — direct equality + isinstance on ALL 4 outputs (no pytest.approx).
    assert all(isinstance(p, Decimal) for p in path)
    # Verify exact 12-fractional-digit preservation.
    assert path[0] == Decimal("65000.123456789012")
    assert path[1] == Decimal("65010.987654321098")
    assert path[2] == Decimal("64990.111111111111")
    assert path[3] == Decimal("65005.555555555555")


def test_generate_intra_candle_path_raises_on_low_above_high() -> None:
    """§G — low > high (malformed OHLC): ValueError."""
    with pytest.raises(ValueError, match="low > high"):
        generate_intra_candle_path(
            open=Decimal("100"),
            high=Decimal("95"),
            low=Decimal("110"),
            close=Decimal("100"),
        )


@pytest.mark.parametrize(
    ("o", "h", "lo", "c", "field"),
    [
        # open above high
        (Decimal("120"), Decimal("110"), Decimal("95"), Decimal("105"), "open"),
        # open below low
        (Decimal("90"), Decimal("110"), Decimal("95"), Decimal("105"), "open"),
        # close above high
        (Decimal("100"), Decimal("110"), Decimal("95"), Decimal("120"), "close"),
        # close below low
        (Decimal("100"), Decimal("110"), Decimal("95"), Decimal("90"), "close"),
    ],
)
def test_generate_intra_candle_path_raises_on_open_or_close_outside_range(
    o: Decimal,
    h: Decimal,
    lo: Decimal,
    c: Decimal,
    field: str,
) -> None:
    """§H — open or close outside [low, high]: ValueError mentions the offending field."""
    with pytest.raises(ValueError, match=rf"{field} outside \[low, high\]"):
        generate_intra_candle_path(open=o, high=h, low=lo, close=c)
