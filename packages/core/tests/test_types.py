"""Test packages.core.types — enum membership."""

from __future__ import annotations

from packages.core.types import (
    Action,
    BotStatus,
    ExchangeMode,
    ExchangeSource,
    IngestionStatus,
    ScoringDecision,
    TradeStatus,
)


def test_enums_have_full_member_sets() -> None:
    """All StrEnums must match DDL enum values verbatim per §7.2."""
    assert {a.value for a in Action} == {"LONG", "SHORT", "CLOSE", "CUSTOM"}
    assert {s.value for s in BotStatus} == {"active", "paused", "archived"}
    assert {m.value for m in ExchangeMode} == {"live", "testnet", "paper"}
    assert {s.value for s in ExchangeSource} == {"binance", "bybit", "custom"}
    assert {s.value for s in TradeStatus} == {"open", "closed", "error"}
    assert {s.value for s in IngestionStatus} == {"validated", "duplicate", "invalid"}
    assert {s.value for s in ScoringDecision} == {"execute", "reject", "passthrough"}
