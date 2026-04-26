"""Invariant tests for :class:`OhlcCandle` and :class:`FeatureValue`."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from packages.features import FeatureValue, OhlcCandle


def _candle() -> OhlcCandle:
    return OhlcCandle(
        symbol="BTCUSDT",
        interval="1m",
        bucket_start=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
        open=Decimal("50000.123456789012"),
        high=Decimal("50100.987654321098"),
        low=Decimal("49999.000000000001"),
        close=Decimal("50050.555555555555"),
        volume=Decimal("12.345678901234"),
        source="binance",
    )


class TestOhlcCandle:
    def test_is_frozen(self) -> None:
        candle = _candle()
        with pytest.raises(FrozenInstanceError):
            candle.symbol = "ETHUSDT"  # type: ignore[misc]

    def test_uses_slots(self) -> None:
        assert hasattr(OhlcCandle, "__slots__")
        assert "symbol" in OhlcCandle.__slots__

    def test_decimal_precision_round_trip_at_numeric_30_12(self) -> None:
        """Decimals at the §7.2 NUMERIC(30,12) precision floor survive intact."""
        candle = _candle()
        assert candle.open == Decimal("50000.123456789012")
        assert candle.high == Decimal("50100.987654321098")
        assert candle.low == Decimal("49999.000000000001")
        assert candle.close == Decimal("50050.555555555555")
        assert candle.volume == Decimal("12.345678901234")

    def test_equality_by_value(self) -> None:
        assert _candle() == _candle()

    def test_distinct_when_field_differs(self) -> None:
        a = _candle()
        b = OhlcCandle(
            symbol=a.symbol,
            interval=a.interval,
            bucket_start=a.bucket_start,
            open=a.open,
            high=a.high,
            low=a.low,
            close=Decimal("50050.555555555556"),
            volume=a.volume,
            source=a.source,
        )
        assert a != b


class TestFeatureValue:
    def test_value_num_constructs(self) -> None:
        fv = FeatureValue(value_num=Decimal("70.5"))
        assert fv.value_num == Decimal("70.5")
        assert fv.value_bool is None
        assert fv.value_json is None

    def test_value_bool_constructs(self) -> None:
        fv = FeatureValue(value_bool=True)
        assert fv.value_bool is True
        assert fv.value_num is None
        assert fv.value_json is None

    def test_value_json_constructs(self) -> None:
        fv = FeatureValue(value_json={"upper": Decimal("1"), "lower": Decimal("-1")})
        assert fv.value_json == {"upper": Decimal("1"), "lower": Decimal("-1")}
        assert fv.value_num is None
        assert fv.value_bool is None

    def test_zero_non_none_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            FeatureValue()

    def test_two_non_none_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            FeatureValue(value_num=Decimal("70"), value_bool=True)

    def test_three_non_none_raises(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            FeatureValue(
                value_num=Decimal("70"),
                value_bool=True,
                value_json={"k": Decimal("1")},
            )

    def test_is_frozen(self) -> None:
        fv = FeatureValue(value_num=Decimal("70"))
        with pytest.raises(FrozenInstanceError):
            fv.value_num = Decimal("80")  # type: ignore[misc]

    def test_value_num_variant_is_hashable(self) -> None:
        fv = FeatureValue(value_num=Decimal("70.5"))
        assert fv in {fv}

    def test_value_bool_variant_is_hashable(self) -> None:
        fv = FeatureValue(value_bool=True)
        assert fv in {fv}

    def test_value_json_variant_is_unhashable(self) -> None:
        """``Mapping`` (dict) is not hashable; this variant cannot enter a set.

        Documented behaviour, not a defect — features whose result is a
        mapping (Bollinger bands, MACD signal/histogram) require the
        caller to normalise before set/dict membership.
        """
        fv = FeatureValue(value_json={"upper": Decimal("1")})
        with pytest.raises(TypeError, match="unhashable"):
            hash(fv)
