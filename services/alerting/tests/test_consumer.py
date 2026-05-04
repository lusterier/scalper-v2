"""Unit tests for :func:`services.alerting.app.consumer.make_alert_handler` (T-409, 6 tests).

Mocks AlertsConfig + DedupTracker + TelegramClient + jinja2.Environment +
logger + now_fn per WG#5 DI pattern. Pin handler flow: parse → find_rule
→ threshold → dedup → render → send.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from jinja2 import DictLoader, Environment

from packages.bus.envelope import MessageEnvelope
from packages.core import CorrelationId
from services.alerting.app.config import (
    AlertsConfig,
    AlertThreshold,
    ChannelConfig,
    RateLimitConfig,
    RuleConfig,
)
from services.alerting.app.consumer import make_alert_handler
from services.alerting.app.dedup import DedupTracker

_T0 = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)


def _make_envelope(payload: dict[str, Any]) -> MessageEnvelope:
    return MessageEnvelope(
        correlation_id=CorrelationId("cid-test"),
        publisher="test",
        payload=payload,
        published_at=_T0,
    )


def _make_alerts_config(
    *,
    rules: list[RuleConfig] | None = None,
    dedup_window_seconds: int = 300,
) -> AlertsConfig:
    if rules is None:
        rules = [
            RuleConfig(
                event="*",
                channel="system",
                severity="info",
                template="default.j2",
            )
        ]
    return AlertsConfig(
        channels={
            "system": ChannelConfig(telegram_chat_id_env="TELEGRAM_CHAT_SYSTEM"),
        },
        rate_limit=RateLimitConfig(dedup_window_seconds=dedup_window_seconds),
        rules=rules,
        channel_chat_ids={"system": "111"},
    )


def _make_jinja_env() -> Environment:
    return Environment(
        loader=DictLoader({"default.j2": "<b>{{ event }}</b>"}),
        autoescape=False,
    )


def _build_handler(
    *,
    alerts_config: AlertsConfig | None = None,
    dedup: DedupTracker | None = None,
    telegram_send: AsyncMock | None = None,
    now_fn: Any = lambda: _T0,
) -> tuple[Any, AsyncMock, MagicMock]:
    cfg = alerts_config or _make_alerts_config()
    dd = dedup or DedupTracker(window_seconds=300)
    send_mock = telegram_send or AsyncMock()
    telegram_client = MagicMock()
    telegram_client.send = send_mock
    logger = MagicMock()
    handler = make_alert_handler(
        alerts_config=cfg,
        dedup=dd,
        telegram_client=telegram_client,
        jinja_env=_make_jinja_env(),
        logger=logger,
        now_fn=now_fn,
    )
    return handler, send_mock, logger


@pytest.mark.asyncio
async def test_handler_drops_payload_missing_event_field() -> None:
    """Envelope without `event` field → log warning + no telegram send."""
    handler, send_mock, logger = _build_handler()
    await handler(_make_envelope({"foo": "bar"}))
    send_mock.assert_not_awaited()
    logger.warning.assert_called_once()
    assert logger.warning.call_args[0][0] == "alert_payload_missing_event"


@pytest.mark.asyncio
async def test_handler_drops_unknown_event_when_no_wildcard() -> None:
    """Specific rule + payload event NOT matching → log info + no send."""
    cfg = _make_alerts_config(
        rules=[
            RuleConfig(
                event="heartbeat_stale",
                channel="system",
                severity="critical",
                template="default.j2",
            )
        ]
    )
    handler, send_mock, logger = _build_handler(alerts_config=cfg)
    await handler(_make_envelope({"event": "unknown_xyz"}))
    send_mock.assert_not_awaited()
    logger.info.assert_called_once()
    assert logger.info.call_args[0][0] == "alert_no_rule_match"


@pytest.mark.asyncio
async def test_handler_renders_template_and_sends() -> None:
    """Wildcard rule + payload → telegram_client.send called with rendered template."""
    handler, send_mock, _logger = _build_handler()
    await handler(_make_envelope({"event": "test_event", "x": 1}))
    send_mock.assert_awaited_once()
    assert send_mock.await_args is not None
    kwargs = send_mock.await_args.kwargs
    assert kwargs["channel"] == "system"
    assert "<b>test_event</b>" in kwargs["text"]
    assert kwargs["is_critical"] is False  # severity=info


@pytest.mark.asyncio
async def test_handler_threshold_gate_drops_below_min() -> None:
    """Threshold field < min → drop + log debug."""
    cfg = _make_alerts_config(
        rules=[
            RuleConfig(
                event="pnl_audit_correction",
                channel="system",
                severity="info",
                template="default.j2",
                threshold=AlertThreshold(field="abs_delta_usd", min=10.0),
            )
        ]
    )
    handler, send_mock, logger = _build_handler(alerts_config=cfg)
    await handler(_make_envelope({"event": "pnl_audit_correction", "abs_delta_usd": 5.0}))
    send_mock.assert_not_awaited()
    # logger.debug called with "alert_below_threshold"
    debug_calls = [c for c in logger.debug.call_args_list if c[0][0] == "alert_below_threshold"]
    assert len(debug_calls) == 1


@pytest.mark.asyncio
async def test_handler_threshold_gate_passes_at_min() -> None:
    """Threshold field == min (boundary inclusive) → send."""
    cfg = _make_alerts_config(
        rules=[
            RuleConfig(
                event="pnl_audit_correction",
                channel="system",
                severity="info",
                template="default.j2",
                threshold=AlertThreshold(field="abs_delta_usd", min=10.0),
            )
        ]
    )
    handler, send_mock, _logger = _build_handler(alerts_config=cfg)
    await handler(_make_envelope({"event": "pnl_audit_correction", "abs_delta_usd": 10.0}))
    send_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_handler_dedup_within_window_skips_send() -> None:
    """Same event twice within dedup window → second call no-op."""
    handler, send_mock, logger = _build_handler()
    payload = {"event": "test_event", "x": 1}
    await handler(_make_envelope(payload))
    await handler(_make_envelope(payload))
    assert send_mock.await_count == 1
    debug_calls = [c for c in logger.debug.call_args_list if c[0][0] == "alert_deduped"]
    assert len(debug_calls) == 1
