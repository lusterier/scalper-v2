"""Test packages.core.types — enum membership."""

from __future__ import annotations

from packages.core.types import Action, BotStatus, ExchangeMode, ExchangeSource


def test_enums_have_full_member_sets() -> None:
    """Action covers §7.2 incl. CUSTOM; BotStatus + ExchangeMode + ExchangeSource match the DDL."""
    assert {a.value for a in Action} == {"LONG", "SHORT", "CLOSE", "CUSTOM"}
    assert {s.value for s in BotStatus} == {"active", "paused", "archived"}
    assert {m.value for m in ExchangeMode} == {"live", "testnet", "paper"}
    assert {s.value for s in ExchangeSource} == {"binance", "bybit", "custom"}
