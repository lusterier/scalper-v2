"""§11.4 + ADR-0003 NATS KV token-bucket shared rate limiter.

T-205 ships :class:`SharedRateLimiter`; T-208 (BybitV5Adapter REST
methods) consumes via DI'd ctor kwarg; T-215 (adapter pool composition
root) instantiates one shared instance for the live-adapter pool.

PaperExchange does NOT consume the limiter (no upstream rate limit).

Design per ADR-0003 (commit ``dcf98bd``):

1. Four bucket families: ``bybit.<sub_account>.orders``,
   ``bybit.<sub_account>.positions``, ``bybit.<sub_account>.market``,
   ``bybit.ip.global``.
2. 500ms coordinated-pause-flag at ``bybit.ip.pause`` — written by
   :meth:`signal_upstream_rate_limit` (caller-driven from T-208 on Bybit
   429); read by :meth:`_wait_if_paused` at the head of every
   :meth:`acquire`.
3. Optimistic CAS via :meth:`packages.bus.NatsClient.kv_update` with
   ``last=revision``; retry-once-on-conflict; fail-open after 3 conflicts.
4. All bucket params as constructor kwargs (Settings env vars per
   §N9; T-215 reads env at composition time).
5. DI-only — :class:`SharedRateLimiter` is per-process instance, NOT
   module-level singleton (§N6).
6. ``sub_account`` keying caller-provided per :meth:`acquire` call.

Decision #14 (H-025 binding): the 500ms shared pause flag IS the
"exponential backoff with jitter per endpoint group" mechanism brief
§20 H-025 line 2802 mandates — additional per-call jitter on lazy-refill
wait or CAS retry is rejected as premature optimization (refill wait is
deterministic; CAS conflicts are diagnostic signal).

Decision #12 (precision): ``tokens`` stored as ``float`` (NOT Decimal)
because rate-limit budget is approximate-by-design — sub-token precision
is irrelevant; lazy-refill arithmetic over fractional seconds favors float
speed over Decimal exactness. NOT a §N1 violation: §5.13 invariant is for
financial math (P&L, prices, qty), NOT for operational counters.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from packages.bus.errors import PublishError
from packages.core import idempotent, non_idempotent, now_utc

if TYPE_CHECKING:
    from collections.abc import Callable

    from packages.bus import NatsClient


__all__ = ["EndpointGroup", "SharedRateLimiter"]

EndpointGroup = Literal[
    "orders", "positions", "market"
]  # T-529: "market" for /v5/market/* endpoints

_BUCKET = "rate_limits"
# T-548: NATS KV keys are `.`-separated, NOT `:`. nats-py's
# nats.js.kv._is_key_valid (VALID_KEY_RE = ^[-/_=.a-zA-Z0-9]+$) rejects
# `:` → InvalidKeyError on every kv_get/kv_put (latent: only a live
# Bybit adapter exercises this path). ADR-0003 / BRIEF §11.4 footnoted.
_PAUSE_KEY = "bybit.ip.pause"
_IP_GLOBAL_KEY = "bybit.ip.global"
_MAX_CAS_RETRIES = 3

logger = logging.getLogger(__name__)


class SharedRateLimiter:
    """Per-process token-bucket coordinator (per ADR-0003).

    State lives in NATS KV bucket ``rate_limits``; the limiter object
    itself holds only the bucket parameters + bus handle.
    """

    def __init__(
        self,
        *,
        bus: NatsClient,
        orders_rate: float,
        orders_capacity: float,
        positions_rate: float,
        positions_capacity: float,
        market_rate: float,
        market_capacity: float,
        ip_global_rate: float,
        ip_global_capacity: float,
        pause_ms: int,
        now_fn: Callable[[], datetime] = now_utc,
    ) -> None:
        self._bus = bus
        self._params: dict[str, tuple[float, float]] = {
            "orders": (orders_rate, orders_capacity),
            "positions": (positions_rate, positions_capacity),
            "market": (market_rate, market_capacity),
            "_ip_global": (ip_global_rate, ip_global_capacity),
        }
        self._pause_ms = pause_ms
        self._now_fn = now_fn

    @non_idempotent
    async def acquire(
        self,
        sub_account: str,
        endpoint_group: EndpointGroup,
    ) -> None:
        """Block until a token can be debited from sub-bucket + IP-global.

        Decision #10 — pause-flag check FIRST. Decision #9 — when local
        bucket empty, ``asyncio.sleep((1-tokens)/rate)`` then retry refill+debit.
        ADR-0003 §3 — CAS retry-once + fail-open after 3 conflicts.

        Order is sequenced (not transactional per OQ-2 default A): debit
        sub-account bucket first, then IP-global. If IP-global fails after
        sub-account succeeded, the sub-account debit is NOT rolled back.
        """
        await self._wait_if_paused()
        sub_key = f"bybit.{sub_account}.{endpoint_group}"
        await self._refill_and_debit(sub_key, endpoint_group)
        await self._refill_and_debit(_IP_GLOBAL_KEY, "_ip_global")

    @idempotent
    async def signal_upstream_rate_limit(self) -> None:
        """Write the ``bybit.ip.pause`` flag (caller-driven from T-208 on Bybit 429).

        OQ-4 default A: limiter does NOT detect 429 internally; T-208
        catches it at the HTTP layer and calls this. ``expires_at = now +
        RATE_LIMIT_PAUSE_MS``. Last-write-wins per :meth:`kv_put`
        precedent (idempotent by ADR-0003 §"Rationale"; Decision #5).
        """
        expires_at = self._now_fn() + timedelta(milliseconds=self._pause_ms)
        await self._bus.kv_put(
            _BUCKET,
            _PAUSE_KEY,
            expires_at.isoformat().encode("utf-8"),
        )

    async def _wait_if_paused(self) -> None:
        """Decision #10 — pause-flag check before any debit.

        Reads ``bybit.ip.pause`` from KV; if present and ``now < expires_at``,
        sleeps until expiry. Malformed value (corrupt ISO-8601) is logged
        and skipped — fail-safe: treat malformed as no-pause.
        """
        result = await self._bus.kv_get(_BUCKET, _PAUSE_KEY)
        if result is None:
            return
        value, _revision = result
        try:
            expires_at = datetime.fromisoformat(value.decode("utf-8"))
        except ValueError:
            logger.warning(
                "rate_limiter.malformed_pause_flag",
                extra={"value": repr(value)},
            )
            return
        now = self._now_fn()
        if now < expires_at:
            await asyncio.sleep((expires_at - now).total_seconds())

    async def _refill_and_debit(self, key: str, group: str) -> None:
        """Lazy-refill + CAS-debit one token (per Decision #9 / ADR-0003 §3).

        Loop body: read state → compute refill → if tokens<1 sleep+retry;
        else debit + CAS-write; on conflict retry up to 3 times; fail-open
        after with WARN log.
        """
        rate, capacity = self._params[group]
        for _attempt in range(_MAX_CAS_RETRIES):
            result = await self._bus.kv_get(_BUCKET, key)
            now = self._now_fn()
            if result is None:
                tokens, last_refill_at, revision = capacity, now, None
            else:
                # CONCERN 2 fix — corrupted KV value (mid-write crash, non-JSON
                # bytes) falls back to fresh-bucket; mirror pause-flag malformed
                # handler shape per Edge case #2.
                try:
                    state = json.loads(result[0].decode("utf-8"))
                    tokens = float(state["tokens"])
                    last_refill_at = datetime.fromisoformat(state["last_refill_at"])
                except (json.JSONDecodeError, KeyError, ValueError):
                    logger.warning(
                        "rate_limiter.malformed_bucket_state",
                        extra={"key": key, "value": repr(result[0])},
                    )
                    tokens, last_refill_at = capacity, now
                    revision = None
                else:
                    revision = result[1]
            elapsed = (now - last_refill_at).total_seconds()
            tokens = min(capacity, tokens + elapsed * rate)
            if tokens < 1.0:
                wait_seconds = (1.0 - tokens) / rate
                await asyncio.sleep(wait_seconds)
                continue
            tokens -= 1.0
            new_value = json.dumps(
                {"tokens": tokens, "last_refill_at": now.isoformat()},
            ).encode("utf-8")
            try:
                if revision is None:
                    await self._bus.kv_put(_BUCKET, key, new_value)
                else:
                    await self._bus.kv_update(_BUCKET, key, new_value, revision)
            except PublishError:
                continue  # CAS conflict — retry on next loop iteration.
            return
        # Fail-open after 3 CAS conflicts (ADR-0003 §3 / Decision #6 / #9).
        logger.warning(
            "rate_limiter.cas_failover_open",
            extra={"key": key, "group": group},
        )
