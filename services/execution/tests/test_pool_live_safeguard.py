"""§N4 unit tests for ``_check_live_mode_safeguard`` (T-520 sub-commit #1).

Verifies BRIEF §16.5:2253-2257 verbatim semantics:

* `exchange_mode == "live"` + `BOT_CONFIRM_LIVE != "yes"` → fail-fast RuntimeError;
  no NATS publish (per WG#3 ordering pin: raise BEFORE publish).
* `exchange_mode == "live"` + `BOT_CONFIRM_LIVE == "yes"` → log WARNING
  `LIVE MODE ENGAGED` + publish `system.alerts` envelope (alerting-svc
  catch-all routes to Telegram per configs/alerts.yaml:32-35).
* `exchange_mode in ("testnet", "paper")` → bypass entirely; no log; no publish.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.db.queries.execution import BotRow
from services.execution.app.pool import _check_live_mode_safeguard

_FIXED_NOW = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


def _now_fn() -> datetime:
    return _FIXED_NOW


def _bot_row(
    *,
    bot_id: str = "alpha",
    exchange_mode: Literal["live", "testnet", "paper", "demo"] = "live",
) -> BotRow:
    return BotRow(
        bot_id=bot_id,
        display_name=f"{bot_id} display",
        exchange_mode=exchange_mode,
    )


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


async def test_live_mode_safeguard_passes_when_confirm_yes() -> None:
    """env=yes → no raise; logger.warning + bus.publish both called."""
    bus = _make_bus()
    logger = MagicMock()
    await _check_live_mode_safeguard(
        bot_row=_bot_row(exchange_mode="live"),
        env={"BOT_CONFIRM_LIVE": "yes"},
        bus=bus,
        bound_logger=logger,
        now_fn=_now_fn,
    )
    logger.warning.assert_called_once()
    assert logger.warning.call_args.args[0] == "LIVE MODE ENGAGED"
    bus.publish.assert_awaited_once()


async def test_live_mode_safeguard_raises_when_confirm_unset() -> None:
    """env unset → RuntimeError; bus.publish NOT called (WG#3 ordering pin)."""
    bus = _make_bus()
    logger = MagicMock()
    with pytest.raises(RuntimeError, match=r"BRIEF §16.5 safeguard"):
        await _check_live_mode_safeguard(
            bot_row=_bot_row(exchange_mode="live"),
            env={},
            bus=bus,
            bound_logger=logger,
            now_fn=_now_fn,
        )
    bus.publish.assert_not_awaited()
    logger.error.assert_called_once()


async def test_live_mode_safeguard_raises_when_confirm_no() -> None:
    """env='no' → RuntimeError; bus.publish NOT called."""
    bus = _make_bus()
    logger = MagicMock()
    with pytest.raises(RuntimeError, match=r"BRIEF §16.5 safeguard"):
        await _check_live_mode_safeguard(
            bot_row=_bot_row(exchange_mode="live"),
            env={"BOT_CONFIRM_LIVE": "no"},
            bus=bus,
            bound_logger=logger,
            now_fn=_now_fn,
        )
    bus.publish.assert_not_awaited()


async def test_live_mode_safeguard_logs_LIVE_MODE_ENGAGED_warning_on_pass() -> None:
    """env=yes → exact log message + kwargs (bot_id + exchange_mode)."""
    bus = _make_bus()
    logger = MagicMock()
    await _check_live_mode_safeguard(
        bot_row=_bot_row(bot_id="alpha", exchange_mode="live"),
        env={"BOT_CONFIRM_LIVE": "yes"},
        bus=bus,
        bound_logger=logger,
        now_fn=_now_fn,
    )
    logger.warning.assert_called_once_with(
        "LIVE MODE ENGAGED",
        bot_id="alpha",
        exchange_mode="live",
    )


async def test_live_mode_safeguard_publishes_system_alerts_on_pass() -> None:
    """WG#2 + BLOCKER#2 fix — NATS publish `system.alerts` with correct payload."""
    bus = _make_bus()
    logger = MagicMock()
    await _check_live_mode_safeguard(
        bot_row=_bot_row(bot_id="alpha", exchange_mode="live"),
        env={"BOT_CONFIRM_LIVE": "yes"},
        bus=bus,
        bound_logger=logger,
        now_fn=_now_fn,
    )
    bus.publish.assert_awaited_once()
    args, _ = bus.publish.await_args
    subject, envelope = args
    assert subject == "system.alerts"
    assert envelope.payload == {
        "event": "live_mode_engaged",
        "bot_id": "alpha",
        "exchange_mode": "live",
    }
    assert envelope.correlation_id == "live-mode-alpha"
    assert envelope.published_at == _FIXED_NOW


async def test_safeguard_bypassed_for_testnet() -> None:
    """exchange_mode=testnet → no check; no log; no publish."""
    bus = _make_bus()
    logger = MagicMock()
    # NO env var; testnet should still bypass.
    await _check_live_mode_safeguard(
        bot_row=_bot_row(exchange_mode="testnet"),
        env={},
        bus=bus,
        bound_logger=logger,
        now_fn=_now_fn,
    )
    logger.warning.assert_not_called()
    logger.error.assert_not_called()
    bus.publish.assert_not_awaited()


async def test_safeguard_bypassed_for_paper() -> None:
    """exchange_mode=paper → no check; no log; no publish."""
    bus = _make_bus()
    logger = MagicMock()
    await _check_live_mode_safeguard(
        bot_row=_bot_row(exchange_mode="paper"),
        env={},
        bus=bus,
        bound_logger=logger,
        now_fn=_now_fn,
    )
    logger.warning.assert_not_called()
    logger.error.assert_not_called()
    bus.publish.assert_not_awaited()


async def test_safeguard_logs_DEMO_MODE_ENGAGED_for_demo() -> None:
    """T-549b / ADR-0017 OQ-1: exchange_mode=demo → WARNING `DEMO MODE
    ENGAGED` (no env-gate, no publish, no RuntimeError).

    Demo trades a real Bybit demo account (real order lifecycle, isolated
    demo funds — no real capital), so it gets a startup advisory but is
    NOT gated like live. Distinct from testnet/paper (silent bypass).
    """
    bus = _make_bus()
    logger = MagicMock()
    # NO env var; demo must not require BOT_CONFIRM_LIVE.
    await _check_live_mode_safeguard(
        bot_row=_bot_row(bot_id="alpha", exchange_mode="demo"),
        env={},
        bus=bus,
        bound_logger=logger,
        now_fn=_now_fn,
    )
    logger.warning.assert_called_once_with(
        "DEMO MODE ENGAGED",
        bot_id="alpha",
        exchange_mode="demo",
    )
    logger.error.assert_not_called()
    bus.publish.assert_not_awaited()
