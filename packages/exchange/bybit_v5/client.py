"""Bybit V5 REST envelope + HMAC-SHA256 signing + retry matrix (T-207).

T-207 ships the low-level HTTP layer. T-208 (BybitV5Adapter REST methods)
wraps this client with ExchangeClient Protocol semantics
(``place_market_order``, ``cancel_order``, ``get_positions``, ...) —
including pre-call rate-limit ``acquire()`` and post-call
``signal_upstream_rate_limit()`` per OQ-3/4 default A decoupling.

Constructor takes 5 kwargs (``api_key``, ``api_secret``, ``base_url``,
``connect_timeout``, ``read_timeout``); T-208 will extend with
adapter-level wrapping (``limiter``, ``bus``, ``sub_account``, ...).

Module constants per ADR-0003 / brief §11.2 (protocol-binding, NOT
operational tunables):

* ``_RECV_WINDOW_MS = 5000`` — Bybit V5 default per docs.
* ``_BASE_BACKOFF_S = (0.5, 1.0, 2.0)`` — §11.2 verbatim retry schedule.
* ``_JITTER_PCT = 0.1`` — ±10% per §11.2 ``+ jitter``.
* ``_RETCODE_RATE_LIMIT / _RETCODE_AUTH / _RETCODE_REJECT`` — §11.3 mapping.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from typing import TYPE_CHECKING, Any, Literal, Self
from urllib.parse import urlencode

import httpx

from packages.exchange.errors import (
    AuthError,
    ExchangeError,
    NetworkTimeout,
    OrderRejected,
    RateLimitError,
)

if TYPE_CHECKING:
    from types import TracebackType


__all__ = ["BybitV5Client"]


_RECV_WINDOW_MS = 5000
_BASE_BACKOFF_S: tuple[float, ...] = (0.5, 1.0, 2.0)
_JITTER_PCT = 0.1
_RETCODE_RATE_LIMIT: frozenset[int] = frozenset({10006, 10016})
_RETCODE_AUTH: frozenset[int] = frozenset({10003, 10004})
_RETCODE_REJECT: frozenset[int] = frozenset({10001, 10005})

logger = logging.getLogger(__name__)


HttpMethod = Literal["GET", "POST", "DELETE"]


class BybitV5Client:
    """Low-level Bybit V5 REST envelope.

    State-free across calls: signing is per-call; httpx.AsyncClient owns
    the connection pool. Caller (T-208) owns method-level decisions
    (retry count, rate-limit acquire/signal, body shape).
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.bybit.com",
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=read_timeout,
            ),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Drain the underlying httpx connection pool."""
        await self._client.aclose()

    async def request(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        retries: int,
    ) -> dict[str, Any]:
        """Execute one Bybit V5 REST call with retry + retCode mapping.

        Returns the ``result`` field of the Bybit envelope on
        ``retCode == 0``. Raises typed exceptions per §11.3:

        * :class:`RateLimitError` on retCode 10006/10016 OR HTTP 429.
        * :class:`AuthError` on retCode 10003/10004 OR HTTP 401/403.
        * :class:`OrderRejected` on retCode 10001/10005 (with
          ``reason=retMsg``).
        * :class:`NetworkTimeout` after ``retries`` attempts time out.
        * :class:`ExchangeError` on other non-zero retCodes or malformed
          responses (with original as ``__cause__``).

        ``retries`` is caller-passed: T-208 passes 3 for idempotent methods
        (§11.2) and 0 for ``place_market_order`` (H-003 zero-retry).
        """
        return await self._request_with_retry(
            method,
            path,
            params=params,
            body=body,
            retries=retries,
        )

    async def _request_with_retry(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        retries: int,
    ) -> dict[str, Any]:
        max_attempts = retries + 1
        for attempt in range(max_attempts):
            try:
                response = await self._send(method, path, params=params, body=body)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout):
                if attempt + 1 >= max_attempts:
                    raise NetworkTimeout(
                        f"{method} {path} timed out after {max_attempts} attempts"
                    ) from None
                await asyncio.sleep(self._backoff_delay(attempt))
                continue

            # HTTP-status mapping (no retry on 4xx auth/rate-limit; retry on 5xx).
            if response.status_code in (401, 403):
                raise AuthError(f"HTTP {response.status_code}: auth failed")
            if response.status_code == 429:
                raise RateLimitError("HTTP 429: rate limit hit")
            if 500 <= response.status_code < 600:
                if attempt + 1 >= max_attempts:
                    raise NetworkTimeout(
                        f"{method} {path} returned HTTP {response.status_code} "
                        f"after {max_attempts} attempts",
                    )
                await asyncio.sleep(self._backoff_delay(attempt))
                continue
            if response.status_code >= 400:
                raise ExchangeError(
                    f"HTTP {response.status_code}: {response.text[:200]}",
                )

            # HTTP 2xx — parse Bybit envelope.
            try:
                envelope = response.json()
            except json.JSONDecodeError as exc:
                raise ExchangeError(
                    f"malformed response body for {method} {path}",
                ) from exc

            retcode = int(envelope.get("retCode", -1))
            retmsg = str(envelope.get("retMsg", ""))
            if retcode == 0:
                result = envelope.get("result", {})
                return result if isinstance(result, dict) else {}
            mapped = self._translate_retcode(retcode, retmsg)
            raise mapped

        # Unreachable in practice — loop either returns or raises.
        raise NetworkTimeout(f"{method} {path} exhausted retries")

    async def _send(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
    ) -> httpx.Response:
        """Sign + dispatch one HTTP request (no retry)."""
        timestamp_ms = time.time_ns() // 1_000_000
        headers = self._sign_request(
            method=method,
            params=params,
            body=body,
            timestamp_ms=timestamp_ms,
        )
        # W#1 — query keys sorted alphabetically for sign+wire parity.
        sorted_params = dict(sorted(params.items())) if params is not None else None
        # W#2 — pre-serialize body once; pass via content= so httpx sends
        # the BYTE-IDENTICAL string to what was signed.
        if body is not None:
            content = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            content = None
        return await self._client.request(
            method,
            path,
            params=sorted_params,
            content=content,
            headers=headers,
        )

    def _sign_request(
        self,
        *,
        method: HttpMethod,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        timestamp_ms: int,
    ) -> dict[str, str]:
        """Compute X-BAPI-* headers per Bybit V5 signing spec.

        Payload: ``f"{timestamp_ms}{api_key}{recv_window}{query_string_or_body_json}"``;
        query string sorted alphabetically (W#1); body serialized compact
        (separators=(',', ':')) with byte-for-byte parity with the wire
        body (W#2).
        """
        if body is not None:
            payload_tail = json.dumps(body, separators=(",", ":"))
        elif params:
            # urlencode + sorted keys — matches outbound URL produced by _send.
            payload_tail = urlencode(sorted(params.items()))
        else:
            payload_tail = ""
        payload = f"{timestamp_ms}{self._api_key}{_RECV_WINDOW_MS}{payload_tail}"
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": str(timestamp_ms),
            "X-BAPI-RECV-WINDOW": str(_RECV_WINDOW_MS),
            "X-BAPI-SIGN": signature,
        }

    @staticmethod
    def _translate_retcode(retcode: int, retmsg: str) -> ExchangeError:
        """Map non-zero Bybit retCode to typed exception per §11.3."""
        if retcode in _RETCODE_RATE_LIMIT:
            return RateLimitError(f"retCode={retcode} retMsg={retmsg}")
        if retcode in _RETCODE_AUTH:
            return AuthError(f"retCode={retcode} retMsg={retmsg}")
        if retcode in _RETCODE_REJECT:
            return OrderRejected(retmsg)
        return ExchangeError(f"retCode={retcode} retMsg={retmsg}")

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        """§11.2 + §F.3 — base * (1 + uniform(-0.1, 0.1)).

        ``attempt`` is 0-indexed; clamps to last entry of
        :data:`_BASE_BACKOFF_S` for attempts beyond the schedule length.
        """
        base = _BASE_BACKOFF_S[min(attempt, len(_BASE_BACKOFF_S) - 1)]
        return base * (1.0 + random.uniform(-_JITTER_PCT, _JITTER_PCT))  # noqa: S311 # nosec B311 — operational retry jitter per §11.2, not cryptographic
