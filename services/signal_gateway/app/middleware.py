"""Rate-limit ASGI middleware for signal-gateway (§9.1 step 3, §16.3, H-006).

Wraps T-015b1's :class:`services.signal_gateway.app.rate_limit.RateLimiter`
into an ASGI middleware that gates ``POST /webhook`` only — ``/health``,
``/ready``, and ``/metrics`` pass through unrate-limited so liveness /
readiness probes and Prometheus scrapes never get throttled. The
limiter itself is policy-free (per-key sliding window); this layer
chooses the key (client IP) and which paths are subject to it.

Wire order per ``docs/modules/signal_gateway.md`` "Pipeline wire order":
``trace_scope`` (T-015a) → :class:`RateLimitMiddleware` (this) →
handler. Rate-limit is intentionally **before** HMAC verify in the
actual wire order — HMAC is O(body_len), rate limit is O(1); a storm
of unsigned traffic gets rejected without spending HMAC CPU (H-006
posture). The §9.1 numbered list documents step ``3`` (rate limit)
after step ``1`` (HMAC); the diff is captured in the module doc.

This middleware is also the canonical observation point for the
``webhook_processing_seconds`` histogram (§15.3 SLO: p99 < 100 ms per
§9.1). Observation wraps the entire ``POST /webhook`` request lifecycle
— including 429 short-circuits and any exception bubbling out of the
handler — so the SLO histogram reflects honest end-to-end latency.
Non-webhook paths (liveness / readiness / metrics) are NOT observed:
probe traffic dominates webhook signal otherwise.

Client-IP extraction precedence: ``X-Real-IP`` → first comma-split of
``X-Forwarded-For`` → ``request.client.host``. The T-014 nginx
configuration sets both ``X-Real-IP`` and ``X-Forwarded-For`` (see
``infra/nginx/nginx.conf:116-117``); the fallback chain works for
direct-to-uvicorn smoke tests too.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .models import WebhookErrorResponse

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp
    from structlog.stdlib import BoundLogger

    from .metrics import Metrics
    from .rate_limit import RateLimiter

__all__ = ["RateLimitMiddleware"]


_UNKNOWN_IP: Final[str] = "unknown"
_WEBHOOK_PATH: Final[str] = "/webhook"
_RATELIMIT_METHOD: Final[str] = "POST"


def _extract_client_ip(request: Request) -> str:
    """Return the best-effort client IP for rate-limit keying.

    Precedence: ``X-Real-IP`` → first non-empty entry of
    ``X-Forwarded-For`` → ``request.client.host``. Returns
    :data:`_UNKNOWN_IP` if all three are missing/empty so the limiter
    still has a key to bucket on (H-006: share-bucket between
    unidentified callers is the protective default).
    """
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    if request.client is not None and request.client.host:
        return request.client.host
    return _UNKNOWN_IP


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter for ``POST /webhook``.

    Constructor injects the shared :class:`RateLimiter` instance plus
    the service :class:`Metrics` and trading-stream
    :class:`structlog.stdlib.BoundLogger` so the middleware can log /
    increment without re-resolving from ``app.state`` per request.
    Non-``POST`` requests and any path other than ``/webhook`` pass
    through untouched (and are NOT observed in the latency histogram —
    probe traffic must not dominate the SLO signal).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        metrics: Metrics,
        logger: BoundLogger,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._metrics = metrics
        self._logger = logger

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Gate ``POST /webhook`` against the limiter; observe latency end-to-end.

        ``try/finally`` ensures :attr:`Metrics.webhook_processing_seconds`
        observes wall time even when the handler raises — exceptions
        count as latency for SLO purposes.
        """
        if request.method != _RATELIMIT_METHOD or request.url.path != _WEBHOOK_PATH:
            return await call_next(request)

        start = time.monotonic()
        try:
            client_ip = _extract_client_ip(request)
            if await self._limiter.check_and_record(client_ip):
                return await call_next(request)
            self._logger.info(
                "signal_rejected",
                reason="rate_limit",
                source_ip=client_ip,
            )
            self._metrics.errors.labels(
                service="signal-gateway",
                error_class="rate_limit",
            ).inc()
            return JSONResponse(
                status_code=429,
                content=WebhookErrorResponse(
                    detail="rate limited",
                    reason="rate_limit",
                ).model_dump(),
            )
        finally:
            self._metrics.webhook_processing_seconds.observe(time.monotonic() - start)
