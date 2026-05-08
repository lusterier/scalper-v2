"""§N4 unit tests for :mod:`packages.bus.payloads` (T-511b2a / T-511b2 / ADR-0010).

Pin the wire-envelope contracts:

* ``ShadowStartPayload`` requires ``parent_kind`` field (Literal["live", "paper"])
  per T-511b2a (no default — strategy-engine producer MUST specify).
* Pydantic Literal validation rejects unknown ``parent_kind`` values.
* ``VariantSpec`` continues to round-trip Decimal overrides verbatim.
* ``TradeClosedPayload`` requires ``parent_kind`` per T-511b2 H-016 hook
  (symmetric s ShadowStartPayload per ADR-0010); ``closed_at`` UTC-validated.
* L-002 active control: ``subject_for_shadow_start`` + ``subject_for_trade_closed``
  helpers must be importable + correct.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.bus.payloads import (
    ShadowStartPayload,
    TradeClosedPayload,
    VariantSpec,
    subject_for_shadow_start,
    subject_for_trade_closed,
)


def test_shadow_start_payload_parent_kind_required() -> None:
    """parent_kind has no default — Pydantic rejects payload without it (T-511b2a / ADR-0010)."""
    with pytest.raises(ValidationError, match="parent_kind"):
        ShadowStartPayload(  # type: ignore[call-arg]
            parent_trade_id=42,
            bot_id="alpha",
            symbol="BTCUSDT",
            side="buy",
            entry_price=Decimal("65000"),
            qty=Decimal("1"),
            variants=[VariantSpec(name="v1", overrides={})],
        )


def test_shadow_start_payload_parent_kind_literal_rejects_invalid() -> None:
    """Literal["live", "paper"] rejects any other string at validation time."""
    with pytest.raises(ValidationError, match="parent_kind"):
        ShadowStartPayload(
            parent_trade_id=42,
            parent_kind="other",  # type: ignore[arg-type]
            bot_id="alpha",
            symbol="BTCUSDT",
            side="buy",
            entry_price=Decimal("65000"),
            qty=Decimal("1"),
            variants=[VariantSpec(name="v1", overrides={})],
        )


@pytest.mark.parametrize("parent_kind", ["live", "paper"])
def test_shadow_start_payload_parent_kind_round_trip(parent_kind: str) -> None:
    """parent_kind round-trips through model_dump(mode='json') + model_validate."""
    original = ShadowStartPayload(
        parent_trade_id=42,
        parent_kind=parent_kind,  # type: ignore[arg-type]
        bot_id="alpha",
        symbol="BTCUSDT",
        side="buy",
        entry_price=Decimal("65000"),
        qty=Decimal("1"),
        variants=[VariantSpec(name="v1", overrides={"sl_pct": Decimal("0.005")})],
    )
    raw = original.model_dump(mode="json")
    restored = ShadowStartPayload.model_validate(raw)
    assert restored.parent_kind == parent_kind
    assert restored.parent_trade_id == 42


# ---------------------------------------------------------------------------
# T-511b2 / ADR-0010 — TradeClosedPayload (H-016 cancel hook envelope)
# ---------------------------------------------------------------------------


def test_subject_helpers_build_correct_subjects() -> None:
    """L-002 active control: helper output verbatim correct."""
    assert subject_for_shadow_start("alpha") == "shadow.start.alpha"
    assert subject_for_trade_closed("alpha") == "trade.closed.alpha"


@pytest.mark.parametrize("parent_kind", ["live", "paper"])
def test_trade_closed_payload_round_trip(parent_kind: str) -> None:
    """TradeClosedPayload round-trips through model_dump(mode='json') + model_validate;
    closed_at preserves UTC offset."""
    closed_at = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    original = TradeClosedPayload(
        parent_trade_id=42,
        parent_kind=parent_kind,  # type: ignore[arg-type]
        bot_id="alpha",
        closed_at=closed_at,
    )
    raw = original.model_dump(mode="json")
    restored = TradeClosedPayload.model_validate(raw)
    assert restored.parent_trade_id == 42
    assert restored.parent_kind == parent_kind
    assert restored.closed_at == closed_at
    assert restored.closed_at.utcoffset() == closed_at.utcoffset()


def test_trade_closed_payload_parent_kind_required() -> None:
    """parent_kind has no default — Pydantic ValidationError when omitted."""
    with pytest.raises(ValidationError, match="parent_kind"):
        TradeClosedPayload(  # type: ignore[call-arg]
            parent_trade_id=42,
            bot_id="alpha",
            closed_at=datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC),
        )


def test_trade_closed_payload_rejects_naive_datetime() -> None:
    """closed_at must be timezone-aware UTC (mirror :func:`_validate_utc`)."""
    naive = datetime(2026, 5, 8, 12, 0, 0)  # noqa: DTZ001 — intentional for validation test
    with pytest.raises(ValidationError, match="timezone-aware"):
        TradeClosedPayload(
            parent_trade_id=42,
            parent_kind="live",
            bot_id="alpha",
            closed_at=naive,
        )


def test_trade_closed_payload_rejects_non_utc_offset() -> None:
    """closed_at non-zero UTC offset is rejected."""
    cest = datetime(2026, 5, 8, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    with pytest.raises(ValidationError, match="UTC"):
        TradeClosedPayload(
            parent_trade_id=42,
            parent_kind="live",
            bot_id="alpha",
            closed_at=cest,
        )


# ---------------------------------------------------------------------------
# T-513a / BRIEF §13.5 — ShadowRejectedStartPayload (rejected-signal observation)
# ---------------------------------------------------------------------------


def test_shadow_rejected_start_payload_round_trip() -> None:
    """ShadowRejectedStartPayload round-trips through model_dump(mode='json') + model_validate."""
    from packages.bus.payloads import ShadowRejectedStartPayload

    rejected_at = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
    original = ShadowRejectedStartPayload(
        signal_id=42,
        bot_id="alpha",
        symbol="BTCUSDT",
        action="LONG",
        virtual_entry_price=Decimal("65000"),
        sl_pct=Decimal("0.005"),
        tp_pct=Decimal("0.01"),
        be_trigger=Decimal("0.005"),
        be_sl_level=Decimal("0.001"),
        rejected_at=rejected_at,
    )
    raw = original.model_dump(mode="json")
    restored = ShadowRejectedStartPayload.model_validate(raw)
    assert restored.signal_id == 42
    assert restored.symbol == "BTCUSDT"
    assert restored.virtual_entry_price == Decimal("65000")
    assert restored.sl_pct == Decimal("0.005")
    assert restored.rejected_at == rejected_at


def test_shadow_rejected_start_payload_rejects_naive_datetime() -> None:
    """rejected_at must be timezone-aware UTC."""
    from packages.bus.payloads import ShadowRejectedStartPayload

    naive = datetime(2026, 5, 8, 12, 0, 0)  # noqa: DTZ001
    with pytest.raises(ValidationError, match="timezone-aware"):
        ShadowRejectedStartPayload(
            signal_id=42,
            bot_id="alpha",
            symbol="BTCUSDT",
            action="LONG",
            virtual_entry_price=Decimal("65000"),
            sl_pct=Decimal("0.005"),
            tp_pct=Decimal("0.01"),
            be_trigger=Decimal("0.005"),
            be_sl_level=Decimal("0.001"),
            rejected_at=naive,
        )


def test_subject_for_shadow_rejected_start() -> None:
    """L-002 helper: subject_for_shadow_rejected_start('alpha') == 'shadow.rejected.start.alpha'."""
    from packages.bus.payloads import subject_for_shadow_rejected_start

    assert subject_for_shadow_rejected_start("alpha") == "shadow.rejected.start.alpha"
    assert subject_for_shadow_rejected_start("beta") == "shadow.rejected.start.beta"
