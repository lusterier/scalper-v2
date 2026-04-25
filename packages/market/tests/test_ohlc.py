"""Unit tests for :class:`packages.market.OhlcPipeline` (T-104b).

Coverage matrix:

* **Frame classification** — ``data.k.x=True`` routes to persist+publish;
  ``False`` to publish-only; missing ``x`` / non-bool / non-kline event /
  missing ``k`` / non-dict ``data`` are dropped (with malformed-frame
  warning where appropriate).
* **Persist + publish ordering** — closed candle calls
  ``insert_ohlc_1m`` first, then ``bus.publish`` with the deterministic
  UUID5 ``Nats-Msg-Id``. Persist failure → no publish; publish failure
  after persist → DB row stands, error logged. In-progress publishes
  use a fresh ``uuid4`` per call (no dedup).
* **Multi-frame ordering** — interleaved in-progress + closed frames
  produce the right sequence of persist + publish calls.
* **Out-of-order closed (older bucket re-arrives)** — pipeline calls
  ``insert_ohlc_1m`` regardless; the ``ON CONFLICT DO UPDATE`` contract
  in T-104a is the actual repair mechanism (verified by integration
  test there).
* **Decimal precision** — string-encoded Binance numerics flow through
  to the insert call as :class:`Decimal` without float drift.
* **Subject + correlation_id construction** — ``market.ohlc.1m.<symbol>``
  + ``f"ohlc:{symbol}:{bucket_start.isoformat()}"``.
* **Per-symbol task lifecycle** — :meth:`start` spawns one task per
  symbol; :meth:`stop` cancels them; ``CancelledError`` propagates
  through the consumer's ``async with`` (no broad ``except`` swallows
  it). ``start`` idempotent on second call. ``stop`` before ``start``
  is a no-op.
* **Per-symbol task isolation** — handler exception on symbol A logs
  but does not kill task A; symbol B unaffected.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from packages.bus import MessageEnvelope  # noqa: TC001  # used as runtime annotation on _FakeBus
from packages.bus.schemas import message_id_for_closed_candle
from packages.market import OhlcPipeline

if TYPE_CHECKING:
    from types import TracebackType


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeFeed:
    """Async iterator backed by an :class:`asyncio.Queue` test driver.

    Tests call :meth:`push` to enqueue a frame; the consumer task
    sees it via ``async for``. :meth:`close` raises StopAsyncIteration
    so the consumer's loop exits and the parent ``async with`` block
    can finish — used to wind down the pipeline cleanly in tests that
    drive frames through the full task path rather than calling
    ``_handle`` directly.
    """

    _EOF: object = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    def push(self, frame: dict[str, Any]) -> None:
        self._queue.put_nowait(frame)

    def close(self) -> None:
        self._queue.put_nowait(self._EOF)

    def __aiter__(self) -> _FakeFeed:
        return self

    async def __anext__(self) -> dict[str, Any]:
        item = await self._queue.get()
        if item is self._EOF:
            raise StopAsyncIteration
        return item  # type: ignore[no-any-return]


class _FakeSubscription:
    def __init__(self, feed: _FakeFeed) -> None:
        self._feed = feed

    async def __aenter__(self) -> _FakeFeed:
        return self._feed

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        return None


class _FakeSubMgr:
    """Stand-in for :class:`SubscriptionManager`.

    ``subscribe(symbol)`` returns an async-CM whose ``__aenter__``
    yields a per-symbol :class:`_FakeFeed` the tests can push frames
    into. Reuses the same feed instance across calls for the same
    symbol (mirrors the refcount semantics from T-102).
    """

    def __init__(self) -> None:
        self.feeds: dict[str, _FakeFeed] = {}
        self.subscribe_calls: list[str] = []

    def subscribe(self, symbol: str) -> _FakeSubscription:
        self.subscribe_calls.append(symbol)
        feed = self.feeds.setdefault(symbol, _FakeFeed())
        return _FakeSubscription(feed)


class _FakeConn:
    """Captures ``execute()`` calls; can be wired to raise on demand."""

    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.error: Exception | None = None

    async def execute(self, sql: str, *args: Any) -> None:
        if self.error is not None:
            raise self.error
        self.execute_calls.append((sql, args))


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


class _FakeBus:
    """Captures ``publish()`` calls; can be wired to raise on demand."""

    def __init__(self) -> None:
        self.publish_calls: list[tuple[str, MessageEnvelope]] = []
        self.error: Exception | None = None

    async def publish(self, subject: str, envelope: MessageEnvelope) -> None:
        if self.error is not None:
            raise self.error
        self.publish_calls.append((subject, envelope))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _logger() -> Any:
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    log = logging.getLogger("test_ohlc")
    log.handlers = []
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return structlog.stdlib.get_logger("test_ohlc")


def _build_pipeline(
    *,
    conn: _FakeConn | None = None,
    bus: _FakeBus | None = None,
    sub_mgr: _FakeSubMgr | None = None,
    stop_timeout: float = 1.0,
) -> tuple[OhlcPipeline, _FakeSubMgr, _FakeConn, _FakeBus]:
    fake_conn = conn or _FakeConn()
    fake_bus = bus or _FakeBus()
    fake_sub_mgr = sub_mgr or _FakeSubMgr()
    pipeline = OhlcPipeline(
        subscription_mgr=fake_sub_mgr,  # type: ignore[arg-type]
        pool=_FakePool(fake_conn),  # type: ignore[arg-type]
        bus=fake_bus,  # type: ignore[arg-type]
        logger=_logger(),
        stop_timeout_seconds=stop_timeout,
    )
    return pipeline, fake_sub_mgr, fake_conn, fake_bus


def _kline_frame(
    symbol: str = "BTCUSDT",
    *,
    is_closed: bool,
    bucket_start_ms: int = 1714046400000,  # 2024-04-25 12:00:00 UTC
    open_: str = "50000.0",
    high: str = "50100.0",
    low: str = "49900.0",
    close: str = "50050.0",
    volume: str = "100.0",
) -> dict[str, Any]:
    """Build a Binance multiplex envelope carrying a kline frame."""
    return {
        "stream": f"{symbol.lower()}@kline_1m",
        "data": {
            "e": "kline",
            "E": bucket_start_ms + 1,
            "s": symbol,
            "k": {
                "t": bucket_start_ms,
                "T": bucket_start_ms + 60000 - 1,
                "s": symbol,
                "i": "1m",
                "o": open_,
                "c": close,
                "h": high,
                "l": low,
                "v": volume,
                "x": is_closed,
            },
        },
    }


# ---------------------------------------------------------------------------
# Frame classification (direct _handle calls)
# ---------------------------------------------------------------------------


async def test_closed_kline_persists_and_publishes() -> None:
    pipeline, _, conn, bus = _build_pipeline()
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=True))
    assert len(conn.execute_calls) == 1
    assert len(bus.publish_calls) == 1
    subject, envelope = bus.publish_calls[0]
    assert subject == "market.ohlc.1m.BTCUSDT"
    assert envelope.payload["is_closed"] is True
    assert envelope.payload["symbol"] == "BTCUSDT"


async def test_in_progress_kline_publishes_only() -> None:
    pipeline, _, conn, bus = _build_pipeline()
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=False))
    assert conn.execute_calls == []
    assert len(bus.publish_calls) == 1
    _, envelope = bus.publish_calls[0]
    assert envelope.payload["is_closed"] is False


async def test_missing_x_drops_frame() -> None:
    pipeline, _, conn, bus = _build_pipeline()
    frame = _kline_frame(is_closed=True)
    del frame["data"]["k"]["x"]
    await pipeline._handle("BTCUSDT", frame)
    assert conn.execute_calls == []
    assert bus.publish_calls == []


async def test_non_bool_x_drops_frame() -> None:
    pipeline, _, conn, bus = _build_pipeline()
    frame = _kline_frame(is_closed=True)
    frame["data"]["k"]["x"] = "true"
    await pipeline._handle("BTCUSDT", frame)
    assert conn.execute_calls == []
    assert bus.publish_calls == []


async def test_non_kline_event_drops() -> None:
    pipeline, _, conn, bus = _build_pipeline()
    frame = _kline_frame(is_closed=True)
    frame["data"]["e"] = "trade"
    await pipeline._handle("BTCUSDT", frame)
    assert conn.execute_calls == []
    assert bus.publish_calls == []


async def test_missing_data_drops() -> None:
    pipeline, _, conn, bus = _build_pipeline()
    await pipeline._handle("BTCUSDT", {"stream": "btcusdt@kline_1m"})
    assert conn.execute_calls == []
    assert bus.publish_calls == []


async def test_non_dict_kline_drops() -> None:
    pipeline, _, conn, bus = _build_pipeline()
    frame = _kline_frame(is_closed=True)
    frame["data"]["k"] = "garbage"
    await pipeline._handle("BTCUSDT", frame)
    assert conn.execute_calls == []
    assert bus.publish_calls == []


# ---------------------------------------------------------------------------
# Persist + publish ordering
# ---------------------------------------------------------------------------


async def test_persist_failure_skips_publish() -> None:
    conn = _FakeConn()
    conn.error = RuntimeError("PG down")
    pipeline, _, _, bus = _build_pipeline(conn=conn)
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=True))
    assert bus.publish_calls == []


async def test_publish_failure_after_persist_does_not_unwind_db() -> None:
    """publish-after-persist contract — DB row stands when bus blows up."""
    bus = _FakeBus()
    bus.error = RuntimeError("NATS down")
    pipeline, _, conn, _ = _build_pipeline(bus=bus)
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=True))
    # DB write happened (1 execute call) even though publish raised.
    assert len(conn.execute_calls) == 1


async def test_in_progress_publish_failure_logs_only() -> None:
    bus = _FakeBus()
    bus.error = RuntimeError("NATS hiccup")
    pipeline, _, conn, _ = _build_pipeline(bus=bus)
    # Should not raise, no DB write.
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=False))
    assert conn.execute_calls == []


# ---------------------------------------------------------------------------
# Multi-frame ordering
# ---------------------------------------------------------------------------


async def test_three_in_progress_then_closed_emits_expected_sequence() -> None:
    pipeline, _, conn, bus = _build_pipeline()
    for _ in range(3):
        await pipeline._handle("BTCUSDT", _kline_frame(is_closed=False))
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=True))
    # 3 publishes (no DB) + 1 INSERT + 1 publish (closed).
    assert len(conn.execute_calls) == 1
    assert len(bus.publish_calls) == 4
    closed_flags = [env.payload["is_closed"] for _, env in bus.publish_calls]
    assert closed_flags == [False, False, False, True]


# ---------------------------------------------------------------------------
# Out-of-order closed
# ---------------------------------------------------------------------------


async def test_out_of_order_closed_still_invokes_insert() -> None:
    """Pipeline doesn't gate on bucket monotonicity — the DB ON CONFLICT contract
    (T-104a) is what actually decides whether the row updates. T-104b's only job
    is to issue the INSERT for every closed frame it sees.
    """
    pipeline, _, conn, _ = _build_pipeline()
    later = _kline_frame(is_closed=True, bucket_start_ms=1714046460000)
    earlier = _kline_frame(is_closed=True, bucket_start_ms=1714046400000)
    await pipeline._handle("BTCUSDT", later)
    await pipeline._handle("BTCUSDT", earlier)
    assert len(conn.execute_calls) == 2


# ---------------------------------------------------------------------------
# Decimal precision + deterministic UUID5 + subject + correlation_id
# ---------------------------------------------------------------------------


async def test_decimal_precision_flows_to_insert_call() -> None:
    """String-encoded Binance numerics survive parse → insert as Decimal."""
    pipeline, _, conn, _ = _build_pipeline()
    frame = _kline_frame(
        is_closed=True,
        open_="50000.123456789012",
        high="50100.000000000001",
        low="49950.999999999999",
        close="50050.555555555555",
        volume="123.456789012345",
    )
    await pipeline._handle("BTCUSDT", frame)
    _sql, args = conn.execute_calls[0]
    # Positional args: (symbol, bucket_start, open, high, low, close, volume, source).
    assert args[2] == Decimal("50000.123456789012")
    assert args[3] == Decimal("50100.000000000001")
    assert args[4] == Decimal("49950.999999999999")
    assert args[5] == Decimal("50050.555555555555")
    assert args[6] == Decimal("123.456789012345")


async def test_closed_publish_uses_deterministic_uuid5() -> None:
    pipeline, _, _, bus = _build_pipeline()
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=True))
    _, envelope = bus.publish_calls[0]
    expected_bucket = datetime(2024, 4, 25, 12, 0, 0, tzinfo=UTC)
    expected_id = message_id_for_closed_candle("BTCUSDT", expected_bucket)
    assert envelope.message_id == expected_id
    assert isinstance(envelope.message_id, UUID)
    assert envelope.message_id.version == 5


async def test_in_progress_publish_uses_uuid4_per_call() -> None:
    """Every intra-bucket tick is its own message — deterministic dedup would lose ticks."""
    pipeline, _, _, bus = _build_pipeline()
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=False))
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=False))
    _, env_a = bus.publish_calls[0]
    _, env_b = bus.publish_calls[1]
    assert env_a.message_id != env_b.message_id
    assert env_a.message_id.version == 4
    assert env_b.message_id.version == 4


async def test_subject_format_is_market_ohlc_1m_symbol() -> None:
    pipeline, _, _, bus = _build_pipeline()
    await pipeline._handle("ETHUSDT", _kline_frame(symbol="ETHUSDT", is_closed=True))
    subject, _ = bus.publish_calls[0]
    assert subject == "market.ohlc.1m.ETHUSDT"


async def test_correlation_id_carries_symbol_and_bucket() -> None:
    pipeline, _, _, bus = _build_pipeline()
    await pipeline._handle("BTCUSDT", _kline_frame(is_closed=True))
    _, envelope = bus.publish_calls[0]
    assert envelope.correlation_id == "ohlc:BTCUSDT:2024-04-25T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Per-symbol task lifecycle
# ---------------------------------------------------------------------------


async def test_start_spawns_one_task_per_symbol() -> None:
    pipeline, sub_mgr, _, _ = _build_pipeline()
    await pipeline.start(["BTCUSDT", "ETHUSDT"])
    # Yield so the per-symbol tasks reach `async with subscribe(...)`.
    for _ in range(3):
        await asyncio.sleep(0)
    assert sub_mgr.subscribe_calls == ["BTCUSDT", "ETHUSDT"]
    await pipeline.stop()


async def test_start_is_idempotent_on_second_call() -> None:
    pipeline, sub_mgr, _, _ = _build_pipeline()
    await pipeline.start(["BTCUSDT"])
    await pipeline.start(["BTCUSDT"])  # second call no-op
    for _ in range(3):
        await asyncio.sleep(0)
    assert sub_mgr.subscribe_calls == ["BTCUSDT"]
    await pipeline.stop()


async def test_stop_before_start_is_noop() -> None:
    pipeline, _, _, _ = _build_pipeline()
    await pipeline.stop()  # should not raise


async def test_start_with_empty_symbols_spawns_no_tasks() -> None:
    pipeline, sub_mgr, _, _ = _build_pipeline()
    await pipeline.start([])
    await pipeline.stop()
    assert sub_mgr.subscribe_calls == []


async def test_stop_cancels_running_tasks_cleanly() -> None:
    pipeline, sub_mgr, _, bus = _build_pipeline()
    await pipeline.start(["BTCUSDT"])
    for _ in range(3):
        await asyncio.sleep(0)
    # Push one frame so the consumer processes something before we cancel.
    sub_mgr.feeds["BTCUSDT"].push(_kline_frame(is_closed=False))
    for _ in range(3):
        await asyncio.sleep(0)
    assert len(bus.publish_calls) == 1
    await pipeline.stop()
    # All tasks done after stop().
    assert all(t.done() for t in pipeline._tasks.values()) or pipeline._tasks == {}


async def test_per_symbol_task_isolation_handler_error_does_not_cancel_task() -> None:
    """A handler exception on symbol A is logged + swallowed; the task keeps running.

    Drive a malformed frame (missing ``k``) followed by a valid in-progress frame
    on the same symbol; the consumer must still process the second frame, proving
    the ``except Exception`` keeps the task alive.

    A second symbol B receives a valid frame independently — proving the
    per-symbol isolation contract (one task per symbol; failure on A does not
    affect B).
    """
    pipeline, sub_mgr, _, bus = _build_pipeline()
    await pipeline.start(["BTCUSDT", "ETHUSDT"])
    for _ in range(3):
        await asyncio.sleep(0)

    # Symbol A: malformed frame that raises inside _handle (force a parse fail
    # by handing a frame whose `t` cannot be cast to int).
    bad_frame = _kline_frame(is_closed=True)
    bad_frame["data"]["k"]["t"] = "not-a-number"
    sub_mgr.feeds["BTCUSDT"].push(bad_frame)
    # Symbol A: valid in-progress frame after the bad one.
    sub_mgr.feeds["BTCUSDT"].push(_kline_frame(is_closed=False))
    # Symbol B: valid frame in parallel.
    sub_mgr.feeds["ETHUSDT"].push(
        _kline_frame(symbol="ETHUSDT", is_closed=False),
    )

    for _ in range(10):
        await asyncio.sleep(0)

    subjects = sorted(subj for subj, _ in bus.publish_calls)
    assert "market.ohlc.1m.BTCUSDT" in subjects
    assert "market.ohlc.1m.ETHUSDT" in subjects

    await pipeline.stop()


async def test_cancelled_error_propagates_through_consume_loop() -> None:
    """``except Exception`` not bare — CancelledError must reach `async with` exit.

    If the consumer's per-frame except swallowed CancelledError, ``stop()``
    would hang past its timeout. This test verifies the task exits well within
    the configured timeout.
    """
    pipeline, _, _, _ = _build_pipeline(stop_timeout=0.5)
    await pipeline.start(["BTCUSDT"])
    for _ in range(3):
        await asyncio.sleep(0)
    # Park the consumer on an empty feed; cancellation must wake it.
    start = asyncio.get_event_loop().time()
    await pipeline.stop()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 0.5, f"stop() took {elapsed}s — CancelledError likely swallowed"


# ---------------------------------------------------------------------------
# Direct _consume_symbol coverage (handler error inside the consume loop)
# ---------------------------------------------------------------------------


async def test_consume_loop_swallows_handler_exception_and_continues() -> None:
    """Pump two frames through the full consume loop: the first raises, the
    second processes normally — proving the per-frame except keeps iteration alive.
    """
    pipeline, sub_mgr, _, bus = _build_pipeline()
    feed = sub_mgr.feeds.setdefault("BTCUSDT", _FakeFeed())

    # Bad frame: int(kline["t"]) explodes on string input.
    bad = _kline_frame(is_closed=True)
    bad["data"]["k"]["t"] = "garbage"
    feed.push(bad)
    feed.push(_kline_frame(is_closed=False))
    feed.close()

    # Drive the consume loop directly so we don't have to manage start/stop.
    await pipeline._consume_symbol("BTCUSDT")

    assert len(bus.publish_calls) == 1
    _, envelope = bus.publish_calls[0]
    assert envelope.payload["is_closed"] is False


# Note: stop()'s `except TimeoutError` warning log path is intentionally
# uncovered (ohlc.py:164-165). Reliable wedge fixtures fight Python 3.11+
# asyncio cancel-state semantics (every await raises CancelledError once
# task.cancel() is called; uncancel() in a loop still resolves before the
# 200 ms test budget). The log path remains in production code as the
# real safety-net for a wedged consumer; we do not fabricate a synthetic
# wedge to inflate coverage per §0.8.
