"""T-512b — mandatory kill-during-variant integration test.

BRIEF §19:2589 (F5 E3 exit criterion) — *"Shadow variants persist across
restart (verified by killing execution-service mid-variant)."*

BRIEF §20:2787 (H-023 hazard test) — verbatim test name
``test_shadow_variant_survives_restart_via_replay``.

Verifies T-512a `resume_active_variants_on_startup` correctly finalizes-or-
resumes pending ``shadow_variants`` rows after a simulated execution-service
restart. Two test functions:

1. :func:`test_shadow_variant_survives_restart_via_replay` — replay-finalize
   path: SL-triggering candle in the replay window → after restart, variant
   is finalized with ``terminal_outcome='sl_hit'`` per H-024 v2 + T-511b1
   shipped truth-table.
2. :func:`test_shadow_variant_resumes_via_live_continuation_after_restart` —
   replay-resume path: no triggering candle → after restart, live continuation
   task spawned via ``ShadowWorker.register_resume_task`` per T-512a contract.

Restart simulation per operator OQ-1=A 2026-05-08: in-process — mirror
existing repo integration patterns (signal-gateway e2e + T-221 reconcile).
True subprocess + SIGTERM is novel-infra deferred (no repo precedent).

Per WG#1 Path A: synthetic ``shadow.start.<bot_id>`` publish → ShadowWorker A
self-INSERTs the row + spawns variant task → variant id captured via DB
SELECT BEFORE stop (WG#2) → ``shadow_worker_A.stop()`` cancels task
mid-flight → cancellation-does-not-finalize contract (shadow_worker.py:388
try/finally; ``update_shadow_variant_terminal`` is INSIDE try:, NOT in
finally:) → fresh ShadowWorker B + ``resume_active_variants_on_startup``.
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
    ShadowStartPayload,
    VariantSpec,
    subject_for_shadow_start,
)
from packages.core import CorrelationId
from packages.db.queries.market_data import insert_ohlc_1m
from services.execution.app.shadow_replay import resume_active_variants_on_startup
from services.execution.app.shadow_worker import ShadowWorker

if TYPE_CHECKING:
    import asyncpg

    from packages.bus import NatsClient
    from services.execution.app.config import Settings


# T-512b helpers --------------------------------------------------------------


async def _seed_bot(conn: object, bot_id: str) -> None:
    """Insert a ``bots`` row so paper_orders.bot_id FK passes."""
    await conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO bots (bot_id, display_name, created_at, status, "
        "exchange_mode, config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, 'active', 'paper', 'sha256:test', $4)",
        bot_id,
        f"T-512b smoke {bot_id}",
        datetime.now(UTC),
        datetime.now(UTC),
    )


async def _seed_paper_trade(
    conn: object,
    *,
    bot_id: str,
    symbol: str,
    side: str,
    entry_price: Decimal,
    qty: Decimal,
    opened_at: datetime,
) -> int:
    """Seed paper_orders + paper_trades; return paper_trade_id.

    paper_trades.open_order_id FK requires a paper_orders row first.
    Both rows mirror :func:`packages.exchange.paper.persistence.insert_paper_order`
    + :func:`packages.exchange.paper.persistence.insert_paper_trade` shipped
    contracts (NUMERIC(20,4) for notional_usd / fees_paid; ``status='open'``).
    """
    order_row = await conn.fetchrow(  # type: ignore[attr-defined]
        "INSERT INTO paper_orders (bot_id, correlation_id, exchange_order_id, "
        "exchange, symbol, side, order_type, qty, price, status, requested_at, "
        "idempotent, meta) VALUES ($1, $2, $3, 'paper', $4, $5, 'market', $6, $7, "
        "'filled', $8, false, '{}'::jsonb) RETURNING id",
        bot_id,
        f"T512b-{uuid.uuid4().hex[:8]}",
        f"paper-{uuid.uuid4().hex[:8]}",
        symbol,
        side,
        qty,
        entry_price,
        opened_at,
    )
    assert order_row is not None
    order_id = int(order_row["id"])
    notional = (qty * entry_price).quantize(Decimal("0.0001"))
    fees = (notional * Decimal("0.0006")).quantize(Decimal("0.0001"))
    trade_row = await conn.fetchrow(  # type: ignore[attr-defined]
        "INSERT INTO paper_trades (bot_id, open_order_id, symbol, side, entry_price, "
        "qty, notional_usd, fees_paid, opened_at, status, meta) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'open', '{}'::jsonb) "
        "RETURNING id",
        bot_id,
        order_id,
        symbol,
        side,
        entry_price,
        qty,
        notional,
        fees,
        opened_at,
    )
    assert trade_row is not None
    return int(trade_row["id"])


async def _seed_ohlc_candles(
    pool: asyncpg.Pool,
    *,
    symbol: str,
    candles: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal]],
) -> None:
    """Bulk-insert ohlc_1m rows. ``candles`` = list of (bucket_start, open, high, low, close).

    Volume fixed at 1.0 (irrelevant to replay path). Source ``binance`` mirrors
    T-104 OhlcPipeline production wiring + T-513a select_latest_close convention.
    All timestamps must be UTC (§N1); per-row INSERT keeps fallback simple per
    plan WG#3 (chunk_time_interval=7d on ohlc_1m hypertable, so test windows
    spanning 10-30min always fit in one chunk; bulk INSERT is safe).
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


def _build_shadow_start_envelope(
    *,
    bot_id: str,
    parent_trade_id: int,
    symbol: str,
    side: str,
    entry_price: Decimal,
    qty: Decimal,
    variant_overrides: dict[str, Decimal | int],
) -> tuple[str, MessageEnvelope]:
    """Build ``(subject, envelope)`` ready for ``bus.publish``."""
    payload = ShadowStartPayload(
        parent_trade_id=parent_trade_id,
        parent_kind="paper",
        bot_id=bot_id,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        entry_price=entry_price,
        qty=qty,
        variants=[VariantSpec(name="default", overrides=variant_overrides)],
    )
    envelope = MessageEnvelope(
        correlation_id=CorrelationId(f"T-512b-{uuid.uuid4().hex[:8]}"),
        publisher="test-execution-integration",
        payload=payload.model_dump(mode="json"),
    )
    return subject_for_shadow_start(bot_id), envelope


async def _capture_variant_id(pool: asyncpg.Pool, *, parent_trade_id: int) -> int:
    """Capture variant_id BEFORE stop() per WG#2 deterministic-id rationale.

    Filters on ``terminated_at IS NULL`` defensively even though contract
    guarantees NULL during in-flight FSM (cancellation-does-not-finalize per
    shadow_worker.py:388 try/finally placement).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM shadow_variants "
            "WHERE parent_trade_id = $1 AND terminated_at IS NULL "
            "LIMIT 1",
            parent_trade_id,
        )
    assert row is not None, "ShadowWorker A did not INSERT shadow_variants row"
    return int(row["id"])


# T-512b tests ----------------------------------------------------------------


async def test_shadow_variant_survives_restart_via_replay(
    pool: asyncpg.Pool,
    bus: NatsClient,
    nats_test_url: str,
    executions_settings: Settings,
) -> None:
    """BRIEF §20:2787 verbatim — replay-finalize path verifies H-023 + E3.

    Setup: paper_trade + variant (mid-flight in ShadowWorker A) + ohlc_1m
    candles where candle 4 low (64600) crosses SL (64675 = 65000 * 0.995).

    Flow: publish shadow.start → wait → capture id → stop A (cancellation
    does NOT finalize per shadow_worker.py:388) → verify NULL → fresh
    ShadowWorker B + resume_active_variants_on_startup → replay finds SL
    in window → finalize.

    Asserts terminal classification ('sl_hit') + non-null realized_pnl /
    mfe_pct / mae_pct (magnitude precision is verified by T-511b1 + T-512a
    unit tests; integration test only verifies wire-up + classification).
    """
    bot_id = f"alpha_{uuid.uuid4().hex[:8]}"
    symbol = "BTCUSDT"
    side = "buy"
    entry_price = Decimal("65000")
    qty = Decimal("0.001")

    # Variant FSM started 10min ago in this synthetic timeline.
    now = datetime.now(UTC)
    opened_at = now - timedelta(minutes=10)

    # OHLC window covers 10min: candles 0..3 oscillate ~65000; candle 4 wicks
    # to 64600 (< SL at 64675); candles 5..8 are post-trigger and irrelevant
    # since replay loop returns True after candle 4 terminal.
    d = Decimal
    candles = [
        (opened_at + timedelta(minutes=0), d("65000"), d("65050"), d("64950"), d("65020")),
        (opened_at + timedelta(minutes=1), d("65020"), d("65080"), d("64980"), d("65010")),
        (opened_at + timedelta(minutes=2), d("65010"), d("65060"), d("64970"), d("65030")),
        (opened_at + timedelta(minutes=3), d("65030"), d("65070"), d("64960"), d("64980")),
        # Candle 4: low 64600 < SL 64675 → triggers SL.
        (opened_at + timedelta(minutes=4), d("64980"), d("65000"), d("64600"), d("64700")),
        (opened_at + timedelta(minutes=5), d("64700"), d("64750"), d("64650"), d("64720")),
        (opened_at + timedelta(minutes=6), d("64720"), d("64760"), d("64680"), d("64740")),
        (opened_at + timedelta(minutes=7), d("64740"), d("64790"), d("64710"), d("64760")),
        (opened_at + timedelta(minutes=8), d("64760"), d("64800"), d("64720"), d("64780")),
    ]
    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        parent_trade_id = await _seed_paper_trade(
            conn,
            bot_id=bot_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            qty=qty,
            opened_at=opened_at,
        )
    await _seed_ohlc_candles(pool, symbol=symbol, candles=candles)

    # ShadowWorker A — runs the variant briefly then gets killed.
    shadow_worker_a = ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=executions_settings.shadow_seed_balance_usd,
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        fee_rate=executions_settings.shadow_fee_rate,
        clock=lambda: datetime.now(UTC),
    )
    await shadow_worker_a.start()

    subject, envelope = _build_shadow_start_envelope(
        bot_id=bot_id,
        parent_trade_id=parent_trade_id,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        qty=qty,
        variant_overrides={
            "be_trigger": Decimal("0"),
            "be_sl_level": Decimal("0"),
            "trail_pct": Decimal("0"),
            "sl_pct": Decimal("0.005"),
            "tp_pct": Decimal("0.01"),
            "tp_qty_pct": Decimal("1"),
            "max_duration_hours": Decimal("4"),
        },
    )
    await bus.publish(subject, envelope)
    # NATS delivery + handler dispatch + INSERT shadow_variants. 0.5s is empirical
    # for unloaded test environment; tracked under L-001 (test-timing not business
    # logic — hardcoded literal acceptable per plan §Hazards).
    await asyncio.sleep(0.5)

    variant_id = await _capture_variant_id(pool, parent_trade_id=parent_trade_id)

    # WG#2 — verify NULL BEFORE stop() to pin the cancellation-does-not-finalize
    # contract from above (mid-flight FSM has no DB write; H-016 finalizer at
    # shadow_worker.py:388 only does bus_unsubscribe + registry pop).
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT terminated_at FROM shadow_variants WHERE id = $1", variant_id
        )
    assert row is not None
    assert row["terminated_at"] is None, "variant should be in-flight pre-stop"

    # Capture the cancelled task BEFORE stop() clears the registry, so we can
    # await drain afterwards (WG#5 cleanup discipline applies symmetrically).
    cancelled_tasks = [t for tasks in shadow_worker_a._active_tasks.values() for t in tasks]
    await shadow_worker_a.stop()
    for t in cancelled_tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t

    # Cancellation-does-not-finalize contract — row STILL NULL post-stop.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT terminated_at FROM shadow_variants WHERE id = $1", variant_id
        )
    assert row is not None
    assert row["terminated_at"] is None, (
        "shadow_worker.stop() must NOT finalize via H-016 finalizer "
        "(see shadow_worker.py:388 try/finally — terminal write is INSIDE try:, "
        "NOT in finally:; cancellation skips the write)"
    )

    # Drain bus_A subscriptions (mirror real shutdown order from main.py:365).
    # Fixture-scoped ``bus`` is reused as ``bus_b`` below (single NATS client
    # per test is sufficient; the shutdown order pin is satisfied because the
    # ``bus.close()`` in the test fixture teardown happens AFTER both worker
    # contexts in ``finally``).

    # Fresh ShadowWorker B + resume_active_variants_on_startup. Worker B is
    # NOT started — resume hooks register tasks via register_resume_task only.
    shadow_worker_b = ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=executions_settings.shadow_seed_balance_usd,
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        fee_rate=executions_settings.shadow_fee_rate,
        clock=lambda: datetime.now(UTC),
    )
    await resume_active_variants_on_startup(
        pool=pool,
        bus=bus,
        settings=executions_settings,
        shadow_worker=shadow_worker_b,
        clock=lambda: datetime.now(UTC),
    )

    # Replay-finalize path: candle 4 SL crossing fired terminal_future during
    # _replay_candle_loop → _finalize_replay_terminal wrote DB row. Assert
    # classification + non-null financial fields. Magnitude precision verified
    # by T-511b1 + T-512a unit tests; this integration test only pins wire-up.
    async with pool.acquire() as conn:
        final_row = await conn.fetchrow(
            "SELECT terminated_at, terminal_outcome, realized_pnl, mfe_pct, mae_pct "
            "FROM shadow_variants WHERE id = $1",
            variant_id,
        )
    assert final_row is not None
    assert final_row["terminated_at"] is not None, "replay must finalize variant"
    assert final_row["terminal_outcome"] == "sl_hit"
    assert final_row["realized_pnl"] is not None
    assert final_row["mfe_pct"] is not None
    assert final_row["mae_pct"] is not None


async def test_shadow_variant_resumes_via_live_continuation_after_restart(
    pool: asyncpg.Pool,
    bus: NatsClient,
    nats_test_url: str,
    executions_settings: Settings,
) -> None:
    """BRIEF §19:2589 — replay-resume path verifies H-023 second branch.

    Setup mirrors test #1 except OHLC has NO triggering candle (prices oscillate
    in [64850, 65150]; SL=64675 not crossed; TP=65650 not crossed). Replay
    exhausts window without terminal → spawns live continuation task per
    T-512a `_live_continuation` closure → registered via ShadowWorker
    `register_resume_task`.

    Asserts: row stays terminated_at NULL post-resume + ShadowWorker B
    `_active_tasks[parent_trade_id]` has ≥1 registered task.

    Cleanup (WG#5 + WG#9): cancel registered task FIRST → drain via
    contextlib.suppress + await → fixture pool.close in conftest teardown
    AFTER drain (otherwise pool.close blocks on outstanding-conn timeout).
    """
    bot_id = f"alpha_{uuid.uuid4().hex[:8]}"
    symbol = "ETHUSDT"
    side = "buy"
    entry_price = Decimal("65000")
    qty = Decimal("0.001")

    now = datetime.now(UTC)
    # 30min ago start; 1h max_duration so resume timeout = 30min remaining
    # (well above test runtime; we cancel before timeout fires).
    opened_at = now - timedelta(minutes=30)

    # No trigger: prices stay in [64850, 65150]; SL=64675 not crossed;
    # TP=65650 not crossed.
    d = Decimal
    candles = [
        (opened_at + timedelta(minutes=i), d("65000"), d("65150"), d("64850"), d("65050"))
        for i in range(29)
    ]

    async with pool.acquire() as conn:
        await _seed_bot(conn, bot_id)
        parent_trade_id = await _seed_paper_trade(
            conn,
            bot_id=bot_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            qty=qty,
            opened_at=opened_at,
        )
    await _seed_ohlc_candles(pool, symbol=symbol, candles=candles)

    shadow_worker_a = ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=executions_settings.shadow_seed_balance_usd,
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        fee_rate=executions_settings.shadow_fee_rate,
        clock=lambda: datetime.now(UTC),
    )
    await shadow_worker_a.start()

    subject, envelope = _build_shadow_start_envelope(
        bot_id=bot_id,
        parent_trade_id=parent_trade_id,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        qty=qty,
        variant_overrides={
            "be_trigger": Decimal("0"),
            "be_sl_level": Decimal("0"),
            "trail_pct": Decimal("0"),
            "sl_pct": Decimal("0.005"),
            "tp_pct": Decimal("0.01"),
            "tp_qty_pct": Decimal("1"),
            "max_duration_hours": Decimal("1"),
        },
    )
    await bus.publish(subject, envelope)
    await asyncio.sleep(0.5)

    variant_id = await _capture_variant_id(pool, parent_trade_id=parent_trade_id)
    cancelled_tasks_a = [t for tasks in shadow_worker_a._active_tasks.values() for t in tasks]
    await shadow_worker_a.stop()
    for t in cancelled_tasks_a:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT terminated_at FROM shadow_variants WHERE id = $1", variant_id
        )
    assert row is not None
    assert row["terminated_at"] is None

    shadow_worker_b = ShadowWorker(
        bus=bus,
        pool=pool,
        seed_balance=executions_settings.shadow_seed_balance_usd,
        slippage_model="fixed_pct",
        slippage_params={"fixed_slippage_pct": Decimal("0")},
        fee_rate=executions_settings.shadow_fee_rate,
        clock=lambda: datetime.now(UTC),
    )
    await resume_active_variants_on_startup(
        pool=pool,
        bus=bus,
        settings=executions_settings,
        shadow_worker=shadow_worker_b,
        clock=lambda: datetime.now(UTC),
    )

    # Resume must register a live continuation task for this parent trade.
    registered_tasks = shadow_worker_b._active_tasks.get(parent_trade_id, [])
    assert len(registered_tasks) >= 1, (
        "resume_active_variants_on_startup must register a live continuation "
        "task via ShadowWorker.register_resume_task when replay window exhausts "
        "without terminal (T-512a `_live_continuation` closure)"
    )

    # Row still NULL — replay-resume path leaves variant active.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT terminated_at FROM shadow_variants WHERE id = $1", variant_id
        )
    assert row is not None
    assert row["terminated_at"] is None

    # WG#5 + WG#9 — cleanup: cancel + drain registered task BEFORE pool teardown
    # so the live-continuation closure's PE / own_sub finally-block releases
    # any acquired connections.
    for t in registered_tasks:
        t.cancel()
    for t in registered_tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await t
