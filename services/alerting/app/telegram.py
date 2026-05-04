"""Telegram Bot API client wrapper (T-409, OQ-2=A).

Uses ``httpx.AsyncClient`` directly against the Telegram Bot API at
``https://api.telegram.org/bot<TOKEN>/sendMessage`` per OQ-2=A — no
``python-telegram-bot`` library dep (~2 MB image bloat avoided). Single
sendMessage POST per alert; HTML parse_mode for rich formatting.

Retry policy per OQ-3=A: critical messages retry ``max_retries`` times
with exponential backoff starting at ``initial_backoff_s``; non-critical
drop on first failure with WARN log. Both knobs flow through Settings DI
(per BLOCKER #1 fix + L-001 active control — env-tunable for incident-
time adjustments without redeploy; mirror T-408 SSE knobs Settings
pattern).

§N3 — :meth:`TelegramClient.send` is ``@non_idempotent``: Telegram does
not guarantee server-side dedup; retried delivery may double-notify.
Marker is documentation/grep-friendliness only per
:mod:`packages.exchange.paper.persistence` precedent (lines 11-15) — no
Protocol conformance test on ``TelegramClient`` (not a Protocol
implementation; F5+ has no plan to make it one).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from packages.core import non_idempotent

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

__all__ = ["TelegramClient"]


_TELEGRAM_API_BASE = "https://api.telegram.org"
_HTTP_TIMEOUT_S = 10.0


class TelegramClient:
    """httpx wrapper for Telegram Bot API ``sendMessage`` per OQ-2=A.

    ``channel_chat_ids`` maps alert-config channel name (system / trading /
    pnl / security) to the resolved env-var chat_id at lifespan startup.
    """

    def __init__(
        self,
        *,
        token: str,
        channel_chat_ids: dict[str, str],
        max_retries: int,
        initial_backoff_s: float,
        logger: BoundLogger,
    ) -> None:
        self._token = token
        self._channel_chat_ids = dict(channel_chat_ids)  # defensive copy
        self._max_retries = max_retries
        self._initial_backoff_s = initial_backoff_s
        self._logger = logger
        self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)

    @non_idempotent
    async def send(self, *, channel: str, text: str, is_critical: bool) -> None:
        """POST one message to Telegram; retry on failure if critical.

        Critical → ``1 + max_retries`` attempts (1 initial + N retries) with
        exponential backoff doubling each step. Non-critical → 1 attempt;
        drop on failure with WARN log (no retry per OQ-3=A).
        """
        chat_id = self._channel_chat_ids.get(channel)
        if chat_id is None:
            self._logger.error(
                "alerting_telegram_unknown_channel",
                channel=channel,
                known=sorted(self._channel_chat_ids),
            )
            return

        url = f"{_TELEGRAM_API_BASE}/bot{self._token}/sendMessage"
        body = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

        max_attempts = 1 + self._max_retries if is_critical else 1
        backoff = self._initial_backoff_s

        for attempt in range(1, max_attempts + 1):
            try:
                resp = await self._client.post(url, json=body)
                resp.raise_for_status()
                return  # success
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                if attempt >= max_attempts:
                    log_event = (
                        "alerting_telegram_failed_critical"
                        if is_critical
                        else "alerting_telegram_failed"
                    )
                    log_call = self._logger.error if is_critical else self._logger.warning
                    log_call(
                        log_event,
                        channel=channel,
                        attempts=attempt,
                        error=str(exc),
                    )
                    return  # drop; no dead-letter queue in F4
                await asyncio.sleep(backoff)
                backoff *= 2  # exponential

    async def aclose(self) -> None:
        """Close the underlying httpx connection pool cleanly."""
        await self._client.aclose()
