"""§N4 unit tests for :mod:`packages.bus.replay_bus` (T-502).

Mock-free: ReplayBus is pure in-process pub/sub. Tests construct real
ReplayBus + real :class:`MessageEnvelope` + register handlers as
capture-list lambdas.

12 tests covering:

* §A timestamp ordering across subjects.
* §B exact subject match.
* §C ``*`` single-token wildcard.
* §D ``>`` multi-token tail wildcard.
* §E multiple subscribers same subject.
* §F same-timestamp tie-break by insertion order.
* §G handler exception swallowed mid-drain.
* §H run_until_empty re-drain after additional publish.
* publish on closed bus raises.
* close idempotent.
* subscribe returns ReplaySubscription handle.
* unsubscribe via active=False skips handler.
* dotted-pattern token-count mismatch (no wildcard) does not match.
* §H insertion_seq monotonic across drain cycles (regression guard).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from packages.bus.envelope import MessageEnvelope
from packages.bus.replay_bus import ReplayBus, ReplaySubscription, _subject_matches
from packages.core.types import CorrelationId


def _envelope(published_at: datetime, payload: dict[str, str] | None = None) -> MessageEnvelope:
    """Helper: construct MessageEnvelope with explicit published_at."""
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=CorrelationId(str(uuid4())),
        publisher="t-502-test",
        published_at=published_at,
        payload=payload or {},
    )


_T_BASE = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


async def test_timestamp_ordering_across_subjects() -> None:
    """§A — 3 messages across 2 subjects deliver in timestamp ASC, not publish order."""
    bus = ReplayBus()
    received: list[tuple[datetime, str]] = []

    async def handler(env: MessageEnvelope) -> None:
        received.append((env.published_at, env.payload.get("tag", "")))

    await bus.subscribe(">", handler)
    # Publish OUT OF ORDER: t=100, t=50, t=150.
    await bus.publish(
        "market.ohlc.1m.BTC",
        _envelope(_T_BASE + timedelta(seconds=100), {"tag": "ohlc-100"}),
    )
    await bus.publish(
        "signals.validated",
        _envelope(_T_BASE + timedelta(seconds=50), {"tag": "signal-50"}),
    )
    await bus.publish(
        "market.ohlc.1m.BTC",
        _envelope(_T_BASE + timedelta(seconds=150), {"tag": "ohlc-150"}),
    )
    await bus.run_until_empty()
    # ASC delivery order — NOT publish order.
    tags = [t for (_ts, t) in received]
    assert tags == ["signal-50", "ohlc-100", "ohlc-150"]


async def test_exact_subject_match() -> None:
    """§B — subscriber on exact subject receives only that subject's messages."""
    bus = ReplayBus()
    received: list[str] = []

    async def handler(env: MessageEnvelope) -> None:
        received.append(env.payload.get("tag", ""))

    await bus.subscribe("market.ohlc.1m.BTC", handler)
    await bus.publish("market.ohlc.1m.BTC", _envelope(_T_BASE, {"tag": "btc"}))
    await bus.publish(
        "market.ohlc.1m.ETH", _envelope(_T_BASE + timedelta(seconds=1), {"tag": "eth"})
    )
    await bus.run_until_empty()
    assert received == ["btc"]


async def test_wildcard_single_token() -> None:
    """§C — ``*`` matches any single token in that position."""
    bus = ReplayBus()
    received: list[str] = []

    async def handler(env: MessageEnvelope) -> None:
        received.append(env.payload.get("tag", ""))

    await bus.subscribe("market.ohlc.*.BTC", handler)
    await bus.publish("market.ohlc.1m.BTC", _envelope(_T_BASE, {"tag": "1m"}))
    await bus.publish(
        "market.ohlc.5m.BTC", _envelope(_T_BASE + timedelta(seconds=1), {"tag": "5m"})
    )
    await bus.publish(
        "market.ohlc.1m.ETH", _envelope(_T_BASE + timedelta(seconds=2), {"tag": "eth"})
    )
    await bus.run_until_empty()
    assert received == ["1m", "5m"]


async def test_wildcard_tail() -> None:
    """§D — ``>`` matches multi-token tail (entire remainder)."""
    bus = ReplayBus()
    received: list[str] = []

    async def handler(env: MessageEnvelope) -> None:
        received.append(env.payload.get("tag", ""))

    await bus.subscribe(">", handler)
    await bus.publish("market.ohlc.1m.BTC", _envelope(_T_BASE, {"tag": "ohlc"}))
    await bus.publish(
        "signals.validated", _envelope(_T_BASE + timedelta(seconds=1), {"tag": "sig"})
    )
    await bus.run_until_empty()
    assert received == ["ohlc", "sig"]


async def test_multiple_subscribers_same_subject() -> None:
    """§E — 2 subscribers on same subject; both handlers invoked once each."""
    bus = ReplayBus()
    a_received: list[str] = []
    b_received: list[str] = []

    async def handler_a(env: MessageEnvelope) -> None:
        a_received.append("a")

    async def handler_b(env: MessageEnvelope) -> None:
        b_received.append("b")

    await bus.subscribe("X", handler_a)
    await bus.subscribe("X", handler_b)
    await bus.publish("X", _envelope(_T_BASE))
    await bus.run_until_empty()
    assert a_received == ["a"]
    assert b_received == ["b"]


async def test_same_timestamp_tie_break_by_insertion_order() -> None:
    """§F + WG#2 — same published_at; insertion_seq tie-break preserves publish order."""
    bus = ReplayBus()
    received: list[str] = []

    async def handler(env: MessageEnvelope) -> None:
        received.append(env.payload.get("tag", ""))

    await bus.subscribe(">", handler)
    same_ts = _T_BASE
    await bus.publish("A", _envelope(same_ts, {"tag": "first"}))
    await bus.publish("B", _envelope(same_ts, {"tag": "second"}))
    await bus.run_until_empty()
    # Publish order preserved within same-timestamp tie.
    assert received == ["first", "second"]


async def test_handler_exception_does_not_kill_drain() -> None:
    """§G + WG#6 — handler raises on first message; second still delivered."""
    bus = ReplayBus()
    received: list[str] = []

    async def handler(env: MessageEnvelope) -> None:
        tag = env.payload.get("tag", "")
        if tag == "boom":
            msg = "synthetic handler failure"
            raise RuntimeError(msg)
        received.append(tag)

    await bus.subscribe(">", handler)
    await bus.publish("X", _envelope(_T_BASE, {"tag": "boom"}))
    await bus.publish("X", _envelope(_T_BASE + timedelta(seconds=1), {"tag": "ok"}))
    await bus.run_until_empty()
    # First handler raised + was swallowed; second delivered cleanly.
    assert received == ["ok"]


async def test_run_until_empty_then_publish_again_drains_new() -> None:
    """§H + WG#7 — drain semantic + insertion_seq monotonic across cycles."""
    bus = ReplayBus()
    received: list[str] = []

    async def handler(env: MessageEnvelope) -> None:
        received.append(env.payload.get("tag", ""))

    await bus.subscribe(">", handler)
    # First batch.
    await bus.publish("X", _envelope(_T_BASE, {"tag": "a"}))
    await bus.publish("X", _envelope(_T_BASE + timedelta(seconds=1), {"tag": "b"}))
    await bus.run_until_empty()
    assert received == ["a", "b"]
    seq_after_first = bus._insertion_seq

    # Second batch — monotonic seq counter (no reset).
    await bus.publish("X", _envelope(_T_BASE + timedelta(seconds=2), {"tag": "c"}))
    assert bus._insertion_seq == seq_after_first + 1
    await bus.run_until_empty()
    assert received == ["a", "b", "c"]


async def test_publish_on_closed_bus_raises() -> None:
    """close() then publish() → RuntimeError per WG#4."""
    bus = ReplayBus()
    await bus.close()
    with pytest.raises(RuntimeError, match="publish on closed"):
        await bus.publish("X", _envelope(_T_BASE))


async def test_subscribe_on_closed_bus_raises() -> None:
    """close() then subscribe() → RuntimeError per WG#4."""
    bus = ReplayBus()
    await bus.close()

    async def handler(env: MessageEnvelope) -> None:
        pass

    with pytest.raises(RuntimeError, match="subscribe on closed"):
        await bus.subscribe("X", handler)


async def test_close_idempotent() -> None:
    """close() twice — second call no-op (no raise)."""
    bus = ReplayBus()
    await bus.close()
    await bus.close()  # must NOT raise


async def test_subscribe_returns_subscription_handle() -> None:
    """subscribe() returns ReplaySubscription with subject_pattern + handler + active=True."""
    bus = ReplayBus()

    async def handler(env: MessageEnvelope) -> None:
        pass

    sub = await bus.subscribe("market.ohlc.>", handler)
    assert isinstance(sub, ReplaySubscription)
    assert sub.subject_pattern == "market.ohlc.>"
    assert sub.handler is handler
    assert sub.active is True


async def test_unsubscribe_via_active_false_skips_handler() -> None:
    """sub.active = False; subsequent run_until_empty skips handler."""
    bus = ReplayBus()
    received: list[str] = []

    async def handler(env: MessageEnvelope) -> None:
        received.append("called")

    sub = await bus.subscribe("X", handler)
    sub.active = False
    await bus.publish("X", _envelope(_T_BASE))
    await bus.run_until_empty()
    assert received == []


def test_dotted_pattern_token_count_mismatch_no_match() -> None:
    """WG#3 boundary — pattern shorter than subject without wildcard does NOT match."""
    assert _subject_matches("a.b", "a.b.c") is False
    assert _subject_matches("a.b.c", "a.b") is False
    # With > tail wildcard, shorter pattern matches longer subject.
    assert _subject_matches("a.>", "a.b.c") is True


# --- T-507a KV stubs ------------------------------------------------------


async def test_kv_get_returns_none() -> None:
    """T-507a: ReplayBus.kv_get returns None unconditionally (replay has no KV state)."""
    bus = ReplayBus()
    assert await bus.kv_get("any-bucket", "any-key") is None


async def test_kv_put_raises_not_implemented() -> None:
    """T-507a: ReplayBus.kv_put raises NotImplementedError (write op meaningless in replay)."""
    bus = ReplayBus()
    with pytest.raises(NotImplementedError, match="kv_put"):
        await bus.kv_put("bucket", "key", b"value")


async def test_kv_update_raises_not_implemented() -> None:
    """T-507a: ReplayBus.kv_update raises NotImplementedError (CAS meaningless in replay)."""
    bus = ReplayBus()
    with pytest.raises(NotImplementedError, match="kv_update"):
        await bus.kv_update("bucket", "key", b"value", last_revision=0)
