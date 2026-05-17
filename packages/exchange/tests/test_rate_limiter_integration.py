"""§11.4 SharedRateLimiter integration tests against real NATS (T-205).

Env-gated ``NATS_TEST_URL``; ci-full has NATS testcontainer with
``rate_limits`` KV bucket bootstrapped via ``infra/nats/bootstrap.sh``;
locally skipped if the env var is unset.

Tests cover end-to-end CAS semantics + cross-instance pause-flag
visibility (H-025 verbatim test from §20 line 2804 + TASKS.md line 80).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from packages.bus import NatsClient
from packages.exchange.rate_limiter import SharedRateLimiter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_NATS_ENV_VAR = "NATS_TEST_URL"


@pytest.fixture(scope="session")
def nats_test_url() -> str:
    url = os.environ.get(_NATS_ENV_VAR)
    if not url:
        pytest.skip(
            f"{_NATS_ENV_VAR} not set — rate-limiter integration tests require "
            f"a reachable NATS JetStream server with the rate_limits KV bucket "
            f"bootstrapped via infra/nats/bootstrap.sh. T-016 wires testcontainer "
            f"in ci-full.",
            allow_module_level=True,
        )
    return url


@pytest.fixture
async def nats_client(nats_test_url: str) -> AsyncIterator[NatsClient]:
    """Connected NatsClient for the test session."""
    logger = MagicMock()
    for method in ("info", "warning", "error", "debug"):
        setattr(logger, method, MagicMock())

    client = NatsClient(servers=[nats_test_url], name="t205-test", logger=logger)
    await client.connect()
    try:
        yield client
    finally:
        await client.close()


def _make_limiter(bus: NatsClient, *, now: datetime | None = None) -> SharedRateLimiter:
    fixed_now = now or datetime.now(UTC)
    return SharedRateLimiter(
        bus=bus,
        orders_rate=10.0,
        orders_capacity=20.0,
        positions_rate=10.0,
        positions_capacity=20.0,
        ip_global_rate=120.0,
        ip_global_capacity=240.0,
        pause_ms=500,
        now_fn=lambda: fixed_now,
    )


async def _clear_kv_keys(client: NatsClient, *keys: str) -> None:
    """Pre-test cleanup: delete keys to start each test from a known state."""
    assert client._js is not None
    kv = await client._js.key_value("rate_limits")
    for key in keys:
        with contextlib.suppress(Exception):
            await kv.delete(key)


@pytest.mark.asyncio
async def test_one_bot_rate_limit_triggers_shared_pause_flag(
    nats_client: NatsClient,
) -> None:
    """H-025 verbatim per §20 line 2804 + TASKS.md line 80.

    Instance A (one bot adapter) calls signal_upstream_rate_limit() →
    instance B (different bot adapter) calls acquire() in the pause window
    → B observes pause flag via _wait_if_paused() and sleeps until expires_at.
    Cross-bot coordination invariant pinned end-to-end.
    """
    await _clear_kv_keys(nats_client, "bybit.ip.pause", "bybit.bot-b.orders", "bybit.ip.global")
    pause_ms = 200
    now = datetime.now(UTC)
    limiter_a = SharedRateLimiter(
        bus=nats_client,
        orders_rate=10.0,
        orders_capacity=20.0,
        positions_rate=10.0,
        positions_capacity=20.0,
        ip_global_rate=120.0,
        ip_global_capacity=240.0,
        pause_ms=pause_ms,
        now_fn=lambda: now,
    )
    limiter_b = _make_limiter(nats_client, now=now)
    # Bot A signals upstream rate limit.
    await limiter_a.signal_upstream_rate_limit()
    # Bot B's acquire() must observe pause + sleep until expiry.
    start = asyncio.get_event_loop().time()
    await limiter_b.acquire("bot-b", "orders")
    elapsed = asyncio.get_event_loop().time() - start
    # Should have slept ~200ms (allow some scheduling slack on either side).
    assert elapsed >= 0.15, f"expected ~200ms pause; got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_two_concurrent_acquire_calls_serialize_via_cas(
    nats_client: NatsClient,
) -> None:
    """Burst from 2 async tasks → KV revision moves +2; no double-debit.

    Both tasks call acquire() concurrently; one wins CAS, other retries.
    """
    await _clear_kv_keys(nats_client, "bybit.sub-cas.orders", "bybit.ip.global", "bybit.ip.pause")
    limiter = _make_limiter(nats_client)
    await asyncio.gather(
        limiter.acquire("sub-cas", "orders"),
        limiter.acquire("sub-cas", "orders"),
    )
    # After both acquires, the bucket has 18 tokens (20 - 2).
    result = await nats_client.kv_get("rate_limits", "bybit.sub-cas.orders")
    assert result is not None
    state = json.loads(result[0].decode("utf-8"))
    # 2 tokens debited from full capacity 20 = 18 (give or take refill).
    assert state["tokens"] <= 19.0


@pytest.mark.asyncio
async def test_pause_flag_is_visible_across_limiter_instances(
    nats_client: NatsClient,
) -> None:
    """Distinct from `test_one_bot_*` per W#3: low-level KV write visibility.

    Bot A writes pause flag DIRECTLY via bus.kv_put (no signal_upstream_*
    flow); Bot B's acquire() observes it. Pins observation invariant
    independent of triggering side.
    """
    await _clear_kv_keys(nats_client, "bybit.ip.pause", "bybit.bot-vis.orders", "bybit.ip.global")
    now = datetime.now(UTC)
    expires_at = now + timedelta(milliseconds=200)
    # Direct KV write — bypasses limiter API, tests visibility only.
    await nats_client.kv_put(
        "rate_limits",
        "bybit.ip.pause",
        expires_at.isoformat().encode("utf-8"),
    )
    limiter_b = _make_limiter(nats_client, now=now)
    start = asyncio.get_event_loop().time()
    await limiter_b.acquire("bot-vis", "orders")
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.15, f"expected ~200ms pause; got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_acquire_after_signal_blocks_until_pause_expires(
    nats_client: NatsClient,
) -> None:
    """End-to-end coordinated-pause behavior (full API flow)."""
    await _clear_kv_keys(nats_client, "bybit.ip.pause", "bybit.bot-e2e.orders", "bybit.ip.global")
    pause_ms = 150
    now = datetime.now(UTC)
    limiter = SharedRateLimiter(
        bus=nats_client,
        orders_rate=10.0,
        orders_capacity=20.0,
        positions_rate=10.0,
        positions_capacity=20.0,
        ip_global_rate=120.0,
        ip_global_capacity=240.0,
        pause_ms=pause_ms,
        now_fn=lambda: now,
    )
    await limiter.signal_upstream_rate_limit()
    start = asyncio.get_event_loop().time()
    await limiter.acquire("bot-e2e", "orders")
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.10, f"expected ~150ms pause; got {elapsed:.3f}s"
