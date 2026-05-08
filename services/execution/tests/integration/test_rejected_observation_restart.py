"""T-513b2 — mandatory kill-during-rejected-observation integration test.

BRIEF §19:2589 (F5 E3 exit criterion) — *"Shadow variants persist across
restart (verified by killing execution-service mid-variant)."*
T-512b shipped 2026-05-08 the variant kill-test (variant half of E3);
T-513b2 closes the rejected-signal kill-test half — full E3 coverage.

BRIEF §20:2790 (H-023 hazard test) — verbatim test name
``test_rejected_signal_shadow_survives_restart_via_replay``.

Verifies T-513b1 ``resume_active_observations_on_startup`` correctly
finalizes-or-resumes pending ``shadow_rejected`` rows after a simulated
execution-service restart. Two test functions:

1. :func:`test_rejected_signal_shadow_survives_restart_via_replay` —
   replay-finalize path: SL-triggering candle in the replay window →
   after restart, observation is finalized with ``terminal_outcome='would_sl'``
   per T-513a 4-outcome classification.
2. :func:`test_rejected_signal_shadow_resumes_via_live_continuation_after_restart` —
   replay-resume path: no triggering candle → after restart, live
   continuation task spawned via ``ShadowRejectedWorker.register_resume_task``
   per T-513b1 contract.

Restart simulation per operator OQ-1=A 2026-05-08: in-process — mirror
T-512b ``test_shadow_restart.py`` precedent (subprocess + SIGTERM is
novel-infra deferred; BRIEF §13.7:2037 verbatim says "Integration: full
variant lifecycle under testcontainers with simulated ticks" — does not
specifically mandate subprocess).

Per WG#1 + #8 Path A: synthetic ``shadow.rejected.start.<bot_id>`` publish →
ShadowRejectedWorker A self-INSERTs the row + spawns observation task →
rejected_id captured via DB SELECT BEFORE stop (WG#5) → ``shadow_rejected_worker_A.stop()``
cancels task mid-flight → cancellation-doesn't-finalize contract
(``shadow_rejected_worker.py:390-395`` try/finally; ``update_shadow_rejected_terminal``
is INSIDE try:, NOT in finally:) → fresh ShadowRejectedWorker B +
``resume_active_observations_on_startup``.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from packages.bus import MessageEnvelope
from packages.bus.payloads import (
    ShadowRejectedStartPayload,
    subject_for_shadow_rejected_start,
)
from packages.core import CorrelationId
from packages.db.queries.market_data import insert_ohlc_1m
from services.execution.app.shadow_rejected_replay import (
    resume_active_observations_on_startup,
)
from services.execution.app.shadow_rejected_worker import ShadowRejectedWorker

if TYPE_CHECKING:
    import asyncpg

    from packages.bus import NatsClient
    from services.execution.app.config import Settings


# T-513b2 helpers --------------------------------------------------------------


async def _seed_bot(conn: object, bot_id: str) -> None:
    """Insert a ``bots`` row (mirror T-512b helper).

    ``shadow_rejected`` has NO FK to ``bots`` per migration 0014:99
    (``bot_id`` is plain TEXT). Bot row is seeded defensively for
    consistency with T-512b test setup; observation FSM does NOT enforce
    bots existence at runtime.
    """
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO bots (bot_id, display_name, created_at, status, "
        "exchange_mode, config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, 'active', 'paper', 'sha256:test', $4)",
        bot_id,
        f"T-513b2 smoke {bot_id}",
        datetime.now(UTC),
        datetime.now(UTC),
    )


async def _seed_ohlc_candles(
    pool: asyncpg.Pool,
    *,
    symbol: str,
    candles: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal]],
) -> None:
    """Bulk-insert ohlc_1m rows. ``candles`` = list of (bucket_start, open, high, low, close).

    Mirror T-512b helper verbatim. Volume fixed at 1.0 (irrelevant to
    replay path). Source ``binance`` mirrors T-104 OhlcPipeline production
    wiring + T-513a `select_latest_close` convention. All timestamps must
    be UTC (§N1); per-row INSERT keeps simple per WG#3 (chunk_time_interval=7d
    on ohlc_1m hypertable so 30min-spanning windows fit in one chunk).
    """
    async with pool.acquire() as conn:
        for bucket_start, open_, high, low, close in candles:
            await insert_ohlc_1m(
                conn,
                symbol=symbol,
                bucket_start=bucket_start,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=Decimal("1"),
                source="binance",
            )


def _build_shadow_rejected_start_envelope(
    *,
    bot_id: str,
    signal_id: int,
    symbol: str,
    action: str,
    virtual_entry_price: Decimal,
    sl_pct: Decimal,
    tp_pct: Decimal,
    be_trigger: Decimal,
    be_sl_level: Decimal,
    rejected_at: datetime,
) -> tuple[str, MessageEnvelope]:
    """Build ``(subject, envelope)`` ready for ``bus.publish``.

    Mirror T-512b ``_build_shadow_start_envelope`` but for
    :class:`ShadowRejectedStartPayload` (NO ``parent_kind``, NO ``qty``,
    NO ``variants``; rejected obs don't trade per BRIEF §13.5).
    """
    payload = ShadowRejectedStartPayload(
        signal_id=signal_id,
        bot_id=bot_id,
        symbol=symbol,
        action=action,  # type: ignore[arg-type]
        virtual_entry_price=virtual_entry_price,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        be_trigger=be_trigger,
        be_sl_level=be_sl_level,
        rejected_at=rejected_at,
    )
    envelope = MessageEnvelope(
        correlation_id=CorrelationId(f"T-513b2-{uuid.uuid4().hex[:8]}"),
        publisher="test-execution-rejected-integration",
        payload=payload.model_dump(mode="json"),
    )
    return subject_for_shadow_rejected_start(bot_id), envelope


async def _capture_rejected_id(pool: asyncpg.Pool, *, signal_id: int) -> int:
    """Capture rejected_id BEFORE stop() per WG#5 deterministic-id rationale.

    Mirror T-512b ``_capture_variant_id`` pattern; signal_id is the T-513a
    payload.signal_id (index ``shadow_rejected_signal`` per migration 0014:121
    → fast lookup). Filters on ``terminated_at IS NULL`` defensively even
    though contract guarantees NULL during in-flight FSM
    (cancellation-doesn't-finalize per shadow_rejected_worker.py:390-395
    try/finally placement).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM shadow_rejected WHERE signal_id = $1 AND terminated_at IS NULL LIMIT 1",
            signal_id,
        )
    assert row is not None, "ShadowRejectedWorker A did not INSERT shadow_rejected row"
    return int(row["id"])


# T-513b2 tests ----------------------------------------------------------------


async def test_rejected_signal_shadow_survives_restart_via_replay(
    pool: asyncpg.Pool,
    bus: NatsClient,
    nats_test_url: str,
    executions_settings: Settings,
) -> None:
    """BRIEF §20:2790 verbatim — replay-finalize path verifies H-023 + E3 (rejected half).

    Setup: bot row + shadow_rejected (mid-flight in ShadowRejectedWorker A) +
    ohlc_1m candles where candle 4 low (64600) crosses SL threshold
    (64675 = 65000 * 0.995).

    Flow: publish shadow.rejected.start → wait → capture id → stop A
    (cancellation does NOT finalize per shadow_rejected_worker.py:390-395) →
    verify NULL → fresh ShadowRejectedWorker B + resume_active_observations_on_startup
    → replay finds SL in window → finalize.

    Asserts terminal classification ('would_sl') + non-null mfe_pct/mae_pct
    (magnitude precision verified by T-513a + T-513b1 unit tests; integration
    test only verifies wire-up + classification per WG#6).

    NOTE: shadow_rejected schema (migration 0014:94-114) has NO realized_pnl
    column — rejected obs don't trade per BRIEF §13.5; only mfe_pct + mae_pct
    asserted (vs T-512b which asserts realized_pnl).
    """
    bot_id = f"alpha_{uuid.uuid4().hex[:8]}"
    symbol = "BTCUSDT"
    action = "LONG"
    virtual_entry_price = Decimal("65000")
    sl_pct = Decimal("0.005")
    tp_pct = Decimal("0.01")
    be_trigger = Decimal("0")
    be_sl_level = Decimal("0")

    # Observation window started 10min ago in this synthetic timeline.
    now = datetime.now(UTC)
    rejected_at = now - timedelta(minutes=10)
    signal_id = int(uuid.uuid4().int % 1_000_000_000)

    # OHLC window covers 10min: candles 0..3 oscillate ~65000; candle 4 wicks
    # to 64600 (< SL at 64675); candles 5..8 are post-trigger and irrelevant
    # since replay loop returns True after candle 4 terminal.
    d = Decimal
    candles = [
        (rejected_at + timedelta(minutes=0), d("65000"), d("65050"), d("64950"), d("65020")),
        (rejected_at + timedelta(minutes=1), d("65020"), d("65080"), d("64980"), d("65010")),
        (rejected_at + timedelta(minutes=2), d("65010"), d("65060"), d("64970"), d("65030")),
        (rejected_at + timedelta(minutes=3), d("65030"), d("65070"), d("64960"), d("64980")),
        # Candle 4: low 64600 < SL 64675 → triggers WOULD_SL.
        (rejected_at + timedelta(minutes=4), d("64980"), d("65000"), d("64600"), d("64700")),
        (rejected_at + timedelta(minutes=5), d("64700"), d("64750"), d("64650"), d("64720")),
        (rejected_at + timedelta(minutes=6), d("64720"), d("64760"), d("64680"), d("64740")),
        (rejected_at + timedelta(minutes=7), d("64740"), d("64790"), d("64710"), d("64760")),
        (rejected_at + timedelta(minutes=8), d("64760"), d("64800"), d("64720"), d("64780")),
    ]
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
    await _seed_ohlc_candles(pool, symbol=symbol, candles=candles)

    # ShadowRejectedWorker A — runs the observation briefly then gets killed.
    shadow_rejected_worker_a = ShadowRejectedWorker(
        bus=bus,
        pool=pool,
        observation_minutes=executions_settings.shadow_rejected_observation_minutes,
        clock=lambda: datetime.now(UTC),
    )
    await shadow_rejected_worker_a.start()

    subject, envelope = _build_shadow_rejected_start_envelope(
        bot_id=bot_id,
        signal_id=signal_id,
        symbol=symbol,
        action=action,
        virtual_entry_price=virtual_entry_price,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        be_trigger=be_trigger,
        be_sl_level=be_sl_level,
        rejected_at=rejected_at,
    )
    await bus.publish(subject, envelope)
    # NATS delivery + handler dispatch + INSERT shadow_rejected. 0.5s is empirical
    # for unloaded test environment; tracked under L-001 (test-timing not business
    # logic — hardcoded literal acceptable per plan §Hazards + WG#8 inline-justified).
    await asyncio.sleep(0.5)

    rejected_id = await _capture_rejected_id(pool, signal_id=signal_id)

    # WG#4(a) — verify NULL BEFORE stop() to pin the cancellation-doesn't-finalize
    # contract from above (mid-flight FSM has no DB write; H-016 finalizer at
    # shadow_rejected_worker.py:390-395 only does bus_unsubscribe + registry pop).
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT terminated_at FROM shadow_rejected WHERE id = $1", rejected_id
        )
    assert row is not None
    assert row["terminated_at"] is None, "observation should be in-flight pre-stop"

    # Capture the cancelled task BEFORE stop() clears the registry, so we can
    # await drain afterwards (WG#11 cleanup discipline applies symmetrically).
    cancelled_task = shadow_rejected_worker_a._active_tasks.get(rejected_id)
    await shadow_rejected_worker_a.stop()
    if cancelled_task is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await cancelled_task

    # WG#4(b) — cancellation-doesn't-finalize contract: row STILL NULL post-stop.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT terminated_at FROM shadow_rejected WHERE id = $1", rejected_id
        )
    assert row is not None
    assert row["terminated_at"] is None, (
        "shadow_rejected_worker.stop() must NOT finalize via H-016 finalizer "
        "(see shadow_rejected_worker.py:390-395 try/finally — terminal write is "
        "INSIDE try:, NOT in finally:; cancellation skips the write)"
    )

    # Fresh ShadowRejectedWorker B + resume_active_observations_on_startup. Worker B
    # is NOT started — resume hooks register tasks via register_resume_task only.
    shadow_rejected_worker_b = ShadowRejectedWorker(
        bus=bus,
        pool=pool,
        observation_minutes=executions_settings.shadow_rejected_observation_minutes,
        clock=lambda: datetime.now(UTC),
    )
    await resume_active_observations_on_startup(
        pool=pool,
        bus=bus,
        settings=executions_settings,
        shadow_rejected_worker=shadow_rejected_worker_b,
        clock=lambda: datetime.now(UTC),
    )

    # WG#4(c) Replay-finalize path: candle 4 SL crossing fired terminal_future during
    # _replay_observation_candle_loop → _finalize_replay_terminal wrote DB row.
    # Assert classification + non-null financial fields. Magnitude precision
    # verified by T-513a + T-513b1 unit tests; this integration test only pins
    # wire-up + classification per WG#6.
    async with pool.acquire() as conn:
        final_row = await conn.fetchrow(
            "SELECT terminated_at, terminal_outcome, mfe_pct, mae_pct "
            "FROM shadow_rejected WHERE id = $1",
            rejected_id,
        )
    assert final_row is not None
    assert final_row["terminated_at"] is not None, "replay must finalize observation"
    assert final_row["terminal_outcome"] == "would_sl"
    assert final_row["mfe_pct"] is not None
    assert final_row["mae_pct"] is not None


async def test_rejected_signal_shadow_resumes_via_live_continuation_after_restart(
    pool: asyncpg.Pool,
    bus: NatsClient,
    nats_test_url: str,
    executions_settings: Settings,
) -> None:
    """BRIEF §19:2589 — replay-resume path verifies H-023 second branch (rejected half).

    Setup mirrors test #1 except OHLC has NO triggering candle (prices oscillate
    in [64850, 65150]; SL=64675 not crossed; TP=65650 not crossed). Replay
    exhausts window without terminal → spawns live continuation task per
    T-513b1 ``_live_continuation`` closure → registered via ShadowRejectedWorker
    ``register_resume_task``.

    Asserts: row stays terminated_at NULL post-resume + ShadowRejectedWorker B
    ``_active_tasks[rejected_id]`` has registered task (1:1 keying — vs T-512b
    1:N parent_trade_id).

    Cleanup (WG#11): cancel registered task FIRST → drain via
    contextlib.suppress + await → fixture pool.close in conftest teardown
    AFTER drain (otherwise pool.close blocks on outstanding-conn timeout).
    """
    bot_id = f"alpha_{uuid.uuid4().hex[:8]}"
    symbol = "ETHUSDT"
    action = "LONG"
    virtual_entry_price = Decimal("65000")
    sl_pct = Decimal("0.005")
    tp_pct = Decimal("0.01")
    be_trigger = Decimal("0")
    be_sl_level = Decimal("0")

    now = datetime.now(UTC)
    # 30min ago start; default 60min observation_minutes → 30min remaining
    # (well above test runtime; we cancel before timeout fires).
    rejected_at = now - timedelta(minutes=30)
    signal_id = int(uuid.uuid4().int % 1_000_000_000)

    # No trigger: prices stay in [64850, 65150]; SL=64675 not crossed;
    # TP=65650 not crossed.
    d = Decimal
    candles = [
        (rejected_at + timedelta(minutes=i), d("65000"), d("65150"), d("64850"), d("65050"))
        for i in range(29)
    ]

    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
    await _seed_ohlc_candles(pool, symbol=symbol, candles=candles)

    shadow_rejected_worker_a = ShadowRejectedWorker(
        bus=bus,
        pool=pool,
        observation_minutes=executions_settings.shadow_rejected_observation_minutes,
        clock=lambda: datetime.now(UTC),
    )
    await shadow_rejected_worker_a.start()

    subject, envelope = _build_shadow_rejected_start_envelope(
        bot_id=bot_id,
        signal_id=signal_id,
        symbol=symbol,
        action=action,
        virtual_entry_price=virtual_entry_price,
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        be_trigger=be_trigger,
        be_sl_level=be_sl_level,
        rejected_at=rejected_at,
    )
    await bus.publish(subject, envelope)
    await asyncio.sleep(0.5)

    rejected_id = await _capture_rejected_id(pool, signal_id=signal_id)
    cancelled_task_a = shadow_rejected_worker_a._active_tasks.get(rejected_id)
    await shadow_rejected_worker_a.stop()
    if cancelled_task_a is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await cancelled_task_a

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT terminated_at FROM shadow_rejected WHERE id = $1", rejected_id
        )
    assert row is not None
    assert row["terminated_at"] is None

    shadow_rejected_worker_b = ShadowRejectedWorker(
        bus=bus,
        pool=pool,
        observation_minutes=executions_settings.shadow_rejected_observation_minutes,
        clock=lambda: datetime.now(UTC),
    )
    await resume_active_observations_on_startup(
        pool=pool,
        bus=bus,
        settings=executions_settings,
        shadow_rejected_worker=shadow_rejected_worker_b,
        clock=lambda: datetime.now(UTC),
    )

    # Resume must register a live continuation task for this observation.
    # 1:1 keying (dict[int, Task]) vs T-512b 1:N (dict[int, list[Task]]).
    registered_task = shadow_rejected_worker_b._active_tasks.get(rejected_id)
    assert registered_task is not None, (
        "resume_active_observations_on_startup must register a live continuation "
        "task via ShadowRejectedWorker.register_resume_task when replay window "
        "exhausts without terminal (T-513b1 `_live_continuation` closure)"
    )

    # Row still NULL — replay-resume path leaves observation active.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT terminated_at FROM shadow_rejected WHERE id = $1", rejected_id
        )
    assert row is not None
    assert row["terminated_at"] is None

    # WG#11 — cleanup: cancel + drain registered task BEFORE pool teardown
    # so the live-continuation closure's bus_unsubscribe finally-block releases
    # any acquired connections (mirror T-512b cleanup discipline).
    registered_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await registered_task
