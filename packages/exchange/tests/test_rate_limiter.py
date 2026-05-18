"""§11.4 + ADR-0003 SharedRateLimiter unit tests (T-205).

§N4 TDD steps 2-5 per plan-doc — tests written FIRST per operator-locked
implementation order. Mock NatsClient (no real NATS); fast feedback loop.

Tests cover:

* Lazy-refill formula (Hand verification §F.1).
* Pause-flag interaction (Hand verification §F.3).
* CAS retry + fail-open (Hand verification §F.2).
* Constructor + idempotency markers (Decisions #4, #5, #8).
* Edge cases: clock skew, corrupted KV value (CONCERN 2 + 3 fix).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.bus.errors import PublishError
from packages.core import is_idempotent, is_non_idempotent
from packages.exchange.rate_limiter import (
    _IP_GLOBAL_KEY,
    _PAUSE_KEY,
    SharedRateLimiter,
)


def _make_bus_mock() -> MagicMock:
    """Mock NatsClient with the 3 KV methods rate_limiter.py uses."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=None)
    bus.kv_put = AsyncMock(return_value=1)
    bus.kv_update = AsyncMock(return_value=2)
    return bus


def _make_limiter(
    *,
    bus: MagicMock | None = None,
    orders_rate: float = 10.0,
    orders_capacity: float = 20.0,
    positions_rate: float = 10.0,
    positions_capacity: float = 20.0,
    market_rate: float = 120.0,
    market_capacity: float = 240.0,
    ip_global_rate: float = 120.0,
    ip_global_capacity: float = 240.0,
    pause_ms: int = 500,
    now: datetime | None = None,
) -> SharedRateLimiter:
    fixed_now = now or datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
    return SharedRateLimiter(
        bus=bus or _make_bus_mock(),
        orders_rate=orders_rate,
        orders_capacity=orders_capacity,
        positions_rate=positions_rate,
        positions_capacity=positions_capacity,
        market_rate=market_rate,
        market_capacity=market_capacity,
        ip_global_rate=ip_global_rate,
        ip_global_capacity=ip_global_capacity,
        pause_ms=pause_ms,
        now_fn=lambda: fixed_now,
    )


# --- Constructor + markers --------------------------------------------------


def test_constructor_accepts_settings_kwargs_per_adr_0003() -> None:
    """ADR-0003 §4: 10 ctor kwargs (4 rate/capacity pairs + pause_ms + bus + now_fn)."""
    limiter = _make_limiter()
    assert limiter._params["orders"] == (10.0, 20.0)
    assert limiter._params["positions"] == (10.0, 20.0)
    assert limiter._params["market"] == (120.0, 240.0)
    assert limiter._params["_ip_global"] == (120.0, 240.0)
    assert limiter._pause_ms == 500


def test_acquire_marker_is_non_idempotent() -> None:
    """Decision #4: replay would double-debit; @non_idempotent."""
    assert is_non_idempotent(SharedRateLimiter.acquire)


def test_signal_upstream_rate_limit_marker_is_idempotent() -> None:
    """Decision #5: last-write-wins on pause-flag; @idempotent."""
    assert is_idempotent(SharedRateLimiter.signal_upstream_rate_limit)


# --- Happy path: debit both buckets ----------------------------------------


@pytest.mark.asyncio
async def test_acquire_decrements_token_in_local_bucket_and_ip_global() -> None:
    """Decision #2: sub-account + IP-global both debited (sequenced)."""
    bus = _make_bus_mock()
    limiter = _make_limiter(bus=bus)
    await limiter.acquire("sub-a", "orders")
    # Two writes: sub-account bucket + IP-global.
    assert bus.kv_put.await_count == 2
    write_keys = [call.args[1] for call in bus.kv_put.await_args_list]
    assert write_keys == ["bybit.sub-a.orders", "bybit.ip.global"]


@pytest.mark.asyncio
async def test_acquire_uses_correct_bucket_key_per_endpoint_group() -> None:
    """positions endpoint group keys ``bybit.<sub>.positions``, not orders."""
    bus = _make_bus_mock()
    limiter = _make_limiter(bus=bus)
    await limiter.acquire("sub-b", "positions")
    write_keys = [call.args[1] for call in bus.kv_put.await_args_list]
    assert write_keys == ["bybit.sub-b.positions", "bybit.ip.global"]


async def test_acquire_market_group_debits_market_bucket() -> None:
    """T-552 regression pin: ``market`` endpoint group keys
    ``bybit.<sub>.market`` and does NOT ``KeyError``. T-529 introduced the
    ``EndpointGroup`` member + the ``get_instrument_info`` caller but omitted
    the ``_params["market"]`` lockstep — ``acquire(...,"market")`` KeyError'd
    on the first real ``get_instrument_info`` during demo-bot placement.
    """
    bus = _make_bus_mock()
    limiter = _make_limiter(bus=bus)
    await limiter.acquire("sub-c", "market")
    write_keys = [call.args[1] for call in bus.kv_put.await_args_list]
    assert write_keys == ["bybit.sub-c.market", "bybit.ip.global"]


@pytest.mark.asyncio
async def test_acquire_separates_sub_accounts() -> None:
    """H-022: per-sub-account isolation; different sub_account → different KV key."""
    bus = _make_bus_mock()
    limiter = _make_limiter(bus=bus)
    await limiter.acquire("sub-a", "orders")
    await limiter.acquire("sub-b", "orders")
    sub_keys = [
        call.args[1] for call in bus.kv_put.await_args_list if call.args[1].startswith("bybit.sub-")
    ]
    assert sub_keys == ["bybit.sub-a.orders", "bybit.sub-b.orders"]


# --- Lazy refill formula (Hand verification §F.1) --------------------------


@pytest.mark.asyncio
async def test_acquire_lazy_refill_formula_pinned() -> None:
    """§F.1: tokens_now = min(capacity, last_tokens + elapsed * rate).

    Setup: rate=10/s, capacity=20, last_tokens=5.0, elapsed=0.5s.
    Refill: tokens_now = min(20, 5.0 + 0.5 * 10) = 10.0 → debit → 9.0.
    """
    last_at = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
    now = datetime(2026, 4, 29, 12, 0, 0, 500_000, tzinfo=UTC)
    state = b'{"tokens": 5.0, "last_refill_at": "%s"}' % last_at.isoformat().encode()
    bus = _make_bus_mock()
    bus.kv_get = AsyncMock(side_effect=[None, (state, 7), None])
    limiter = _make_limiter(bus=bus, now=now)
    await limiter.acquire("sub-a", "orders")
    # First write is sub-account bucket → assert tokens_after_debit = 9.0.
    first_call = bus.kv_update.await_args_list[0]
    written = first_call.args[2]
    import json

    payload = json.loads(written.decode("utf-8"))
    assert payload["tokens"] == 9.0


@pytest.mark.asyncio
async def test_acquire_blocks_when_local_bucket_empty_then_proceeds_after_refill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision #9: tokens<1 → asyncio.sleep((1-tokens)/rate) then loop.

    Setup: rate=10/s, last_tokens=0.5, elapsed=0 → tokens_now=0.5 → wait_seconds=0.05.
    """
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
    # kv_get sequence: pause-flag check (None) → sub-account iter1 (tokens=0.5 → sleep)
    # → sub-account iter2 (tokens=2.0 → debit) → IP-global (None → fresh bucket).
    state_low = b'{"tokens": 0.5, "last_refill_at": "%s"}' % now.isoformat().encode()
    state_high = b'{"tokens": 2.0, "last_refill_at": "%s"}' % now.isoformat().encode()
    bus = _make_bus_mock()
    bus.kv_get = AsyncMock(side_effect=[None, (state_low, 7), (state_high, 8), None])
    limiter = _make_limiter(bus=bus, now=now)
    await limiter.acquire("sub-a", "orders")
    # First sleep: (1 - 0.5) / 10 = 0.05.
    assert sleep_calls[0] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_acquire_creates_fresh_bucket_when_kv_get_returns_none() -> None:
    """Edge case #6: kv_get None → fresh bucket at full capacity → kv_put (no revision)."""
    bus = _make_bus_mock()
    bus.kv_get = AsyncMock(return_value=None)
    limiter = _make_limiter(bus=bus)
    await limiter.acquire("sub-a", "orders")
    # No kv_update called (no revision for first debit).
    assert bus.kv_update.await_count == 0
    # Two kv_put calls: sub-account + IP-global.
    assert bus.kv_put.await_count == 2


# --- Pause flag (Hand verification §F.3) -----------------------------------


@pytest.mark.asyncio
async def test_acquire_blocks_when_pause_flag_active_until_expires_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§F.3: kv_get(_PAUSE_KEY) → expires_at; sleep((expires_at - now).total_seconds())."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
    expires_at = now + timedelta(milliseconds=300)
    pause_value = expires_at.isoformat().encode("utf-8")

    bus = _make_bus_mock()
    bus.kv_get = AsyncMock(side_effect=[(pause_value, 42), None, None])
    limiter = _make_limiter(bus=bus, now=now)
    await limiter.acquire("sub-a", "orders")
    # First sleep is 0.3s (pause-flag wait).
    assert sleep_calls[0] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_acquire_skips_pause_flag_when_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past expires_at → no sleep; proceed with normal acquire."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
    past_expires = now - timedelta(milliseconds=300)
    pause_value = past_expires.isoformat().encode("utf-8")

    bus = _make_bus_mock()
    bus.kv_get = AsyncMock(side_effect=[(pause_value, 42), None, None])
    limiter = _make_limiter(bus=bus, now=now)
    await limiter.acquire("sub-a", "orders")
    # No sleep call (or no positive sleep at all).
    assert all(s <= 0 for s in sleep_calls)


@pytest.mark.asyncio
async def test_acquire_handles_missing_pause_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kv_get returns None → no sleep; proceed."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    bus = _make_bus_mock()  # default kv_get returns None
    limiter = _make_limiter(bus=bus)
    await limiter.acquire("sub-a", "orders")
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_signal_upstream_rate_limit_writes_pause_flag_with_correct_expiry() -> None:
    """OQ-4 default A: expires_at = now + pause_ms; ISO-8601 UTC."""
    bus = _make_bus_mock()
    now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
    limiter = _make_limiter(bus=bus, pause_ms=500, now=now)
    await limiter.signal_upstream_rate_limit()
    bus.kv_put.assert_awaited_once()
    bucket, key, value = bus.kv_put.await_args.args
    assert bucket == "rate_limits"
    assert key == "bybit.ip.pause"
    expected = (now + timedelta(milliseconds=500)).isoformat().encode("utf-8")
    assert value == expected


@pytest.mark.asyncio
async def test_signal_upstream_rate_limit_idempotent_on_replay() -> None:
    """Decision #5: replay yields same final KV state (last-write-wins).

    Two calls with same now_fn write same value; semantics OK for
    coordinated rebroadcast across N adapters seeing same RateLimitError.
    """
    bus = _make_bus_mock()
    limiter = _make_limiter(bus=bus)
    await limiter.signal_upstream_rate_limit()
    await limiter.signal_upstream_rate_limit()
    assert bus.kv_put.await_count == 2
    # Both writes have same key + same value (now_fn is fixed).
    first = bus.kv_put.await_args_list[0].args
    second = bus.kv_put.await_args_list[1].args
    assert first == second


# --- CAS retry + fail-open (Hand verification §F.2) ------------------------


@pytest.mark.asyncio
async def test_acquire_retries_on_cas_conflict_up_to_3_times_then_fails_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ADR-0003 §3: 3-conflict fail-open with WARN log.

    bus.kv_update raises PublishError 3 times → loop exits → WARN log
    emitted with key + group; no exception propagated.
    """
    import logging

    state = b'{"tokens": 10.0, "last_refill_at": "2026-04-29T12:00:00+00:00"}'
    bus = _make_bus_mock()
    bus.kv_get = AsyncMock(return_value=(state, 7))
    bus.kv_update = AsyncMock(side_effect=PublishError("CAS conflict"))
    limiter = _make_limiter(bus=bus)
    with caplog.at_level(logging.WARNING, logger="packages.exchange.rate_limiter"):
        # No raise; fail-open is silent at API surface (only logs).
        await limiter.acquire("sub-a", "orders")
    assert bus.kv_update.await_count >= 3
    failover_records = [r for r in caplog.records if r.message == "rate_limiter.cas_failover_open"]
    assert len(failover_records) >= 1


# --- Edge cases ------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_handles_clock_skew_negative_elapsed() -> None:
    """Edge case #3: last_refill_at > now → negative elapsed → tokens may decrease.

    Verifies no exception; min(capacity, tokens + neg*rate) yields valid Float
    that remains ≥1 → debit proceeds. Setup: tokens=10.0, elapsed=-0.5s,
    rate=10 → tokens_now = min(20, 10.0 + (-0.5)*10) = 5.0 → debit → 4.0.
    """
    future_at = datetime(2026, 4, 29, 12, 0, 0, 500_000, tzinfo=UTC)  # 0.5s ahead
    now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC)
    state = b'{"tokens": 10.0, "last_refill_at": "%s"}' % future_at.isoformat().encode()
    bus = _make_bus_mock()
    bus.kv_get = AsyncMock(side_effect=[None, (state, 7), None])
    limiter = _make_limiter(bus=bus, now=now)
    await limiter.acquire("sub-a", "orders")
    # No exception; debit succeeded with positive tokens after skew.
    assert bus.kv_update.await_count == 1


@pytest.mark.asyncio
async def test_acquire_falls_back_to_fresh_bucket_on_corrupted_kv_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CONCERN 2: corrupted JSON → log WARN + fresh bucket (kv_put, no revision)."""
    import logging

    bus = _make_bus_mock()
    bus.kv_get = AsyncMock(return_value=(b"not-json", 42))
    limiter = _make_limiter(bus=bus)
    with caplog.at_level(logging.WARNING, logger="packages.exchange.rate_limiter"):
        await limiter.acquire("sub-a", "orders")
    # Used kv_put (fresh bucket) instead of kv_update with stale revision.
    assert bus.kv_put.await_count >= 1
    malformed_records = [
        r for r in caplog.records if r.message == "rate_limiter.malformed_bucket_state"
    ]
    assert len(malformed_records) >= 1


def test_rate_limiter_kv_keys_are_valid_nats_kv_keys() -> None:
    """T-548 regression pin: every rate-limiter NATS KV key must pass the
    real nats-py gate ``nats.js.kv._is_key_valid`` (the exact fn get()/put()
    invoke). The pre-T-548 colon-separated convention raises InvalidKeyError
    on nats-py 2.14.0 (``VALID_KEY_RE = ^[-/_=.a-zA-Z0-9]+$``; ``:`` not in
    class) — latent because only a live Bybit adapter exercises the
    kv_get/kv_put path (ci-fast mocks the bus; integration tests skipped).
    Pure import, no NATS_TEST_URL / round-trip — pins the corrected
    ``.``-separated convention so the bug cannot silently recur."""
    import nats.js.kv

    sub_account, endpoint_group = "sub-demo", "orders"
    sample_sub_key = f"bybit.{sub_account}.{endpoint_group}"  # mirrors rate_limiter.py acquire()
    for key in (_PAUSE_KEY, _IP_GLOBAL_KEY, sample_sub_key):
        assert nats.js.kv._is_key_valid(key) is True, f"invalid NATS KV key: {key!r}"
