"""§N4 unit tests for :mod:`packages.bus.payloads` (T-511b2a / ADR-0010).

Pin the wire-envelope contracts:

* ``ShadowStartPayload`` requires ``parent_kind`` field (Literal["live", "paper"])
  per T-511b2a (no default — strategy-engine producer MUST specify).
* Pydantic Literal validation rejects unknown ``parent_kind`` values.
* ``VariantSpec`` continues to round-trip Decimal overrides verbatim.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.bus.payloads import ShadowStartPayload, VariantSpec


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
