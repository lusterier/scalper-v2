"""Unit tests for :class:`services.alerting.app.telegram.TelegramClient` (T-409, 4 tests).

Uses ``httpx.MockTransport`` for deterministic API mocking — no real
network calls. Mirror existing test patterns for httpx usage.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from services.alerting.app.telegram import TelegramClient


def _build_client_with_transport(
    transport: httpx.MockTransport,
    *,
    max_retries: int = 3,
    initial_backoff_s: float = 0.0,  # 0.0 keeps tests fast (no real sleep)
) -> TelegramClient:
    client = TelegramClient(
        token="test-token",
        channel_chat_ids={"system": "111", "trading": "222"},
        max_retries=max_retries,
        initial_backoff_s=initial_backoff_s,
        logger=MagicMock(),
    )
    # Replace internal httpx.AsyncClient with one using MockTransport.
    client._client = httpx.AsyncClient(transport=transport)
    return client


@pytest.mark.asyncio
async def test_send_success_one_call() -> None:
    """Mock returns 200 → send returns; one POST call to api.telegram.org."""
    call_count = [0]

    def _handler(_request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(_handler)
    client = _build_client_with_transport(transport)
    await client.send(channel="system", text="<b>test</b>", is_critical=False)
    assert call_count[0] == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_send_critical_retries_on_5xx() -> None:
    """Mock returns 500 always; critical send → 1 + max_retries attempts; ERROR log."""
    call_count = [0]

    def _handler(_request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(500, json={"ok": False})

    transport = httpx.MockTransport(_handler)
    logger = MagicMock()
    client = TelegramClient(
        token="test-token",
        channel_chat_ids={"system": "111"},
        max_retries=3,
        initial_backoff_s=0.0,
        logger=logger,
    )
    client._client = httpx.AsyncClient(transport=transport)
    await client.send(channel="system", text="boom", is_critical=True)
    assert call_count[0] == 4  # 1 initial + 3 retries
    # ERROR log for critical failure path.
    logger.error.assert_called_once()
    args, kwargs = logger.error.call_args
    assert args[0] == "alerting_telegram_failed_critical"
    assert kwargs.get("channel") == "system"
    assert kwargs.get("attempts") == 4
    await client.aclose()


@pytest.mark.asyncio
async def test_send_non_critical_drops_on_first_failure() -> None:
    """Mock returns 500; non-critical send → 1 attempt only; WARN log."""
    call_count = [0]

    def _handler(_request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(500)

    transport = httpx.MockTransport(_handler)
    logger = MagicMock()
    client = TelegramClient(
        token="test-token",
        channel_chat_ids={"system": "111"},
        max_retries=3,
        initial_backoff_s=0.0,
        logger=logger,
    )
    client._client = httpx.AsyncClient(transport=transport)
    await client.send(channel="system", text="meh", is_critical=False)
    assert call_count[0] == 1  # no retry for non-critical
    logger.warning.assert_called_once()
    assert logger.warning.call_args[0][0] == "alerting_telegram_failed"
    await client.aclose()


@pytest.mark.asyncio
async def test_send_uses_correct_chat_id_per_channel() -> None:
    """channel='trading' → POST body chat_id == TELEGRAM_CHAT_TRADING value (222)."""
    captured: list[dict[str, Any]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(_handler)
    client = _build_client_with_transport(transport)
    await client.send(channel="trading", text="hello", is_critical=False)
    assert len(captured) == 1
    assert captured[0]["chat_id"] == "222"
    assert captured[0]["text"] == "hello"
    assert captured[0]["parse_mode"] == "HTML"
    await client.aclose()
