"""Test packages.core.types — enum membership."""

from __future__ import annotations

from packages.core.types import (
    Action,
    BotStatus,
    ExchangeMode,
    ExchangeSource,
    IngestionStatus,
    ScoringDecision,
    TradeLifecycleState,
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
    # T-533a — 13-state TradeLifecycleState; StrEnum value == name.lower().
    assert {s.value for s in TradeLifecycleState} == {
        "signal_received",
        "order_requested",
        "order_placed",
        "open",
        "partially_closed",
        "tp_hit",
        "breakeven_set",
        "trailing_active",
        "closing",
        "closed",
        "failed",
        "orphaned",
        "reconciled",
    }
    assert all(s.value == s.name.lower() for s in TradeLifecycleState)
