"""Unit tests for :class:`FeatureUpdate` + :func:`subject_for` (§8.4, §8.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from packages.bus.schemas import FeatureUpdate, subject_for


def _make_payload(
    *,
    feature_name: str = "ind.btcusdt.15m.ema_20",
    symbol: str = "BTCUSDT",
    computed_at: datetime | None = None,
    value_num: float | None = 50050.0,
    value_bool: bool | None = None,
    value_json: dict[str, object] | None = None,
    source_version: str = "builtin.ema.v1",
) -> FeatureUpdate:
    """Build a :class:`FeatureUpdate` with sensible defaults for one variant.

    ``value_num`` defaults to a populated float so the exactly-one
    invariant is satisfied; pass ``value_num=None`` plus one of the
    other two when exercising bool / json variants.
    """
    if computed_at is None:
        computed_at = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
    return FeatureUpdate(
        feature_name=feature_name,
        symbol=symbol,
        computed_at=computed_at,
        value_num=value_num,
        value_bool=value_bool,
        value_json=value_json,
        source_version=source_version,
    )


def test_value_num_constructs() -> None:
    fu = _make_payload(value_num=70.5)
    assert fu.value_num == 70.5
    assert fu.value_bool is None
    assert fu.value_json is None


def test_value_bool_constructs() -> None:
    fu = _make_payload(value_num=None, value_bool=True)
    assert fu.value_bool is True
    assert fu.value_num is None
    assert fu.value_json is None


def test_value_json_constructs() -> None:
    fu = _make_payload(
        value_num=None,
        value_json={"upper": 50100.5, "middle": 50000.0, "lower": 49899.5},
    )
    assert fu.value_json == {"upper": 50100.5, "middle": 50000.0, "lower": 49899.5}


def test_zero_non_none_raises() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _make_payload(value_num=None)


def test_two_non_none_raises() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _make_payload(value_num=70.5, value_bool=True)


def test_three_non_none_raises() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _make_payload(value_num=70.5, value_bool=True, value_json={"k": 1.0})


def test_frozen() -> None:
    fu = _make_payload()
    with pytest.raises(ValueError, match="frozen"):
        fu.value_num = 100.0


def test_naive_computed_at_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _make_payload(computed_at=datetime(2026, 4, 26, 12, 0))  # noqa: DTZ001


def test_cest_computed_at_rejected() -> None:
    cest = timezone(timedelta(hours=2))
    with pytest.raises(ValueError, match="utcoffset must be zero"):
        _make_payload(computed_at=datetime(2026, 4, 26, 14, 0, tzinfo=cest))


def test_zero_offset_normalised_to_utc() -> None:
    """Aware datetime with zero utcoffset but non-UTC tzinfo is normalised."""
    zero_offset = timezone(timedelta(0))
    fu = _make_payload(computed_at=datetime(2026, 4, 26, 12, 0, tzinfo=zero_offset))
    assert fu.computed_at.tzinfo is UTC


def test_iso_serialization_includes_plus_0000() -> None:
    fu = _make_payload(computed_at=datetime(2026, 4, 26, 12, 0, tzinfo=UTC))
    dumped = fu.model_dump(mode="json")
    assert dumped["computed_at"] == "2026-04-26T12:00:00+00:00"


def test_schema_version_pinned_to_1_0() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        FeatureUpdate(
            schema_version="2.0",  # type: ignore[arg-type]
            feature_name="ind.x",
            symbol="BTCUSDT",
            computed_at=datetime(2026, 4, 26, 12, 0, tzinfo=UTC),
            value_num=1.0,
            source_version="v1",
        )


def test_subject_for_matches_spec_literal_verbatim() -> None:
    """Verifies §8.1 line 1213 literal ``features.updated.<feature_name>.<symbol>``.

    The literal lives only in :func:`subject_for` (production) and in
    this test (verification). Verified repo-wide by ``rg "features.updated"
    packages/bus/schemas/ tests/`` per the L-002 control documented in
    ``docs/plans/T-110b.md`` §"Verification plan".
    """
    assert (
        subject_for("ind.btcusdt.15m.ema_20", "BTCUSDT")
        == "features.updated.ind.btcusdt.15m.ema_20.BTCUSDT"
    )


def test_value_json_accepts_non_float_values() -> None:
    """L-004 contract — ``Mapping[str, object]`` is open to non-float values.

    Domain :class:`FeatureValue.value_json` is ``Mapping[str, object]``;
    the wire schema must not narrow it to ``dict[str, float]``. A future
    indicator emitting ``{"signal": "BUY", "score": 0.8}`` must round-trip
    through this Pydantic schema unchanged.
    """
    fu = _make_payload(
        value_num=None,
        value_json={"signal": "BUY", "score": 0.8, "trigger": True},
    )
    assert fu.value_json == {"signal": "BUY", "score": 0.8, "trigger": True}
