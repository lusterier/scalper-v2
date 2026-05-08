"""T-507b — `python scripts/backtest.py` CLI orchestrator (BRIEF §12.2:1949).

Composes 6 components into single in-process backtest:

* T-502 :class:`packages.bus.ReplayBus` — in-process timestamp-ordered pub/sub
* T-503 :class:`packages.exchange.paper.HistoricalOHLCSource` — OHLC replay
* T-504 :class:`packages.exchange.paper.HistoricalSignalSource` — signals replay
* T-506 :class:`packages.exchange.paper.PaperExchange` (replay-mode) —
  intra-candle SL/TP detection
* strategy-engine :func:`services.strategy_engine.app.consumer.make_signal_handler`
* execution-service :func:`services.execution.app.placement.make_per_bot_handler`
  + :class:`services.execution.app.dispatcher.ExecutionDispatcher` +
  :func:`services.execution.app.dispatcher.run_dispatcher_for_bot`

End-to-end FSM: argparse + UTC validation → load + override + hash YAML →
``insert_backtest_run`` (queued) → ``update_backtest_run_to_running`` →
compose ReplayBus + sources + paper + 2 handlers + dispatcher → drain
coordination (``asyncio.gather`` signal+replay; ``bus.run_until_empty``;
cancel dispatcher) → ``copy_paper_trades_to_backtest`` → compute summary
→ ``update_backtest_run_completion`` (completed | failed).

T-507b OQ-D=C Belt-and-suspenders ReplayClock: virtual replay clock
advanced per OHLC bucket + per signal received_at; all components receive
``now_fn=replay_clock.now`` so paper trade timestamps match historical
replay-virtual time (NOT wall-clock today).

OQ-A=A raw-bytes config_yaml + SHA256 (matches analytics-api convention
at ``services/analytics_api/app/routers/backtests.py:206`` for cross-source
audit lookup). ``--override`` mutates raw YAML dict; ``yaml.safe_dump``
round-trip is non-identity (comments stripped, formatting normalised) —
hash diverges from any user-pasted YAML when overrides applied
(operator-acknowledged limitation; documented in operator-runbook).

ADR-0008: PF=None for both edge cases (no losses OR no wins).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import logging
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

import yaml

from packages.bus import MessageEnvelope, ReplayBus
from packages.bus.schemas import SignalValidated
from packages.bus.schemas.orders import subject_for_orders_request
from packages.core import BotId, CorrelationId
from packages.core.replay_clock import ReplayClock
from packages.core.types import BacktestStatus
from packages.db.pool import create_pool
from packages.db.queries.analytics import (
    copy_paper_trades_to_backtest,
    insert_backtest_run,
    update_backtest_run_completion,
    update_backtest_run_to_running,
)
from packages.exchange.paper import PaperExchange
from packages.exchange.paper.historical_ohlc_source import HistoricalOHLCSource
from packages.exchange.paper.historical_signal_source import HistoricalSignalSource
from packages.observability.logging import configure, get_logger
from packages.scoring import FeatureResolver, load_bot_config_from_string
from packages.scoring.registry import load_plugin_registry
from services.execution.app.config import Settings as ExecutionSettings
from services.execution.app.dispatcher import ExecutionDispatcher, run_dispatcher_for_bot
from services.execution.app.placement import make_per_bot_handler
from services.strategy_engine.app.consumer import make_signal_handler

if TYPE_CHECKING:
    from uuid import UUID

    from packages.bus import BusProtocol
    from packages.exchange.paper.adapter import SlippageModel
    from packages.scoring import BotConfig

logger = logging.getLogger(__name__)


__all__ = ["main"]


# Backtest-mode defaults (paper-engine knobs not in BotConfig). Operator can
# override via --override on raw YAML, or per-task ENV in future iterations.
_DEFAULT_SEED_BALANCE = Decimal("10000.00")
_DEFAULT_SLIPPAGE_MODEL: SlippageModel = "fixed_pct"
_DEFAULT_SLIPPAGE_PARAMS: dict[str, Decimal] = {"fixed_slippage_pct": Decimal("0.0005")}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backtest",
        description="Run a single-bot backtest replay (BRIEF §12.2:1949).",
    )
    parser.add_argument("--bot", required=True, help="bot_id matching bots row")
    parser.add_argument(
        "--from",
        dest="from_at",
        required=True,
        help="ISO-8601 UTC start (e.g. 2026-04-01T00:00:00+00:00)",
    )
    parser.add_argument(
        "--to",
        dest="to_at",
        required=True,
        help="ISO-8601 UTC end (must be > --from; tzinfo must be UTC)",
    )
    parser.add_argument(
        "--config-path",
        dest="config_path",
        required=True,
        type=Path,
        help="Path to bot YAML config file",
    )
    parser.add_argument(
        "--override",
        dest="overrides",
        action="append",
        default=[],
        help="YAML dotted-path override 'path.to.field=value'; repeatable",
    )
    parser.add_argument(
        "--pace",
        choices=["1x", "10x", "max"],
        default="max",
        help="OHLC replay pace (T-503; default 'max' = no wait)",
    )
    parser.add_argument(
        "--source",
        choices=["binance", "bybit"],
        default="binance",
        help="OHLC source filter (T-503; default 'binance')",
    )
    parser.add_argument(
        "--plugin-registry-path",
        dest="plugin_registry_path",
        type=Path,
        default=None,
        help="Plugin registry YAML path (default $PLUGIN_REGISTRY_PATH env)",
    )
    parser.add_argument(
        "--db-url",
        dest="db_url",
        default=None,
        help="PostgreSQL DSN (default $DATABASE_URL env)",
    )
    parser.add_argument(
        "--name",
        dest="name",
        default=None,
        help="backtest_runs.name override (default: cli-{bot}-{from}-{to})",
    )
    parser.add_argument(
        "--notes",
        dest="notes",
        default=None,
        help="Optional freeform notes column",
    )
    return parser


def _parse_utc_datetime(label: str, raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        msg = f"--{label} value {raw!r} is not valid ISO-8601: {exc}"
        sys.stderr.write(msg + "\n")
        raise SystemExit(2) from exc
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        msg = f"--{label} must be UTC (tzinfo='+00:00'); got {raw!r}"
        sys.stderr.write(msg + "\n")
        raise SystemExit(2)
    return dt.astimezone(UTC)


def _apply_overrides(yaml_dict: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """Walk + mutate raw YAML dict per --override 'path.to.field=value' spec."""
    for kv in overrides:
        path, sep, value_raw = kv.partition("=")
        if not sep:
            msg = f"--override {kv!r} missing '=' separator"
            raise ValueError(msg)
        value = yaml.safe_load(value_raw)  # auto-typing per YAML scalar
        segments = path.split(".")
        cursor: Any = yaml_dict
        for seg in segments[:-1]:
            if seg.isdigit() and isinstance(cursor, list):
                cursor = cursor[int(seg)]
            elif isinstance(cursor, dict) and seg in cursor:
                cursor = cursor[seg]
            else:
                msg = f"--override path {path!r} does not exist on YAML"
                raise ValueError(msg)
        last = segments[-1]
        if last.isdigit() and isinstance(cursor, list):
            cursor[int(last)] = value
        elif isinstance(cursor, dict):
            cursor[last] = value
        else:
            msg = f"--override path {path!r} does not exist on YAML"
            raise ValueError(msg)
    return yaml_dict


def _load_bot_config_with_overrides(
    *,
    config_path: Path,
    overrides: list[str],
    plugin_registry: Any,
) -> tuple[str, str, BotConfig]:
    """Load raw bytes from config_path; apply overrides via YAML dict mutation;
    re-serialise (only if overrides applied; else identity); hash; validate.

    Per OQ-A=A raw-bytes-first: hash matches analytics-api convention. Per
    CONCERN #4 hash-non-identity caveat: yaml.safe_dump round-trip strips
    comments + normalises formatting → hash diverges from user-pasted YAML
    even if --override does not change values. Documented for operator.
    """
    raw_bytes = config_path.read_bytes()
    if overrides:
        yaml_dict = yaml.safe_load(raw_bytes)
        yaml_dict = _apply_overrides(yaml_dict, overrides)
        config_yaml = yaml.safe_dump(yaml_dict, sort_keys=False, allow_unicode=True)
    else:
        config_yaml = raw_bytes.decode("utf-8")
    config_hash = hashlib.sha256(config_yaml.encode("utf-8")).hexdigest()
    bot_config = load_bot_config_from_string(config_yaml, plugin_registry=plugin_registry)
    return config_yaml, config_hash, bot_config


async def _publish_signals(
    source: HistoricalSignalSource,
    bus: BusProtocol,
    bot_config: BotConfig,
    replay_clock: ReplayClock,
) -> None:
    """Yield SignalRow from T-504; advance clock; reconstruct SignalValidated; publish.

    SignalRow is @dataclass (NOT Pydantic) — explicit field-by-field
    construction. expires_at derived as received_at + bot.signals.ttl_seconds
    (matches H-008 TTL spec).
    """
    async for signal_row in source:
        # OQ-D=C: advance clock to signal timestamp BEFORE publish.
        replay_clock.set(signal_row.received_at)
        # Skip CLOSE per BRIEF §9.4 v1 contract; LONG/SHORT only.
        if signal_row.action not in ("LONG", "SHORT"):
            continue
        signal_validated = SignalValidated(
            schema_version="1.0",
            source=signal_row.source,
            idempotency_key=signal_row.idempotency_key,
            received_at=signal_row.received_at,
            symbol=signal_row.symbol,
            original_symbol=signal_row.original_symbol or signal_row.symbol,
            # Action StrEnum (DB) → Literal (SignalValidated) via .value cast.
            # signal_row.action is filtered to LONG/SHORT above; .value is str
            # but mypy needs explicit Literal cast for SignalValidated.action.
            action=cast("Literal['LONG', 'SHORT', 'CLOSE']", signal_row.action.value),
            expires_at=signal_row.received_at + timedelta(seconds=bot_config.signals.ttl_seconds),
            payload=signal_row.payload,
        )
        envelope = MessageEnvelope(
            correlation_id=CorrelationId(f"backtest-{uuid4()}"),
            published_at=signal_row.received_at,  # heap-sort key per T-502
            publisher="backtest-cli",
            payload=signal_validated.model_dump(),
        )
        await bus.publish("signals.validated", envelope)


async def _compute_summary(
    conn: Any,
    *,
    run_id: UUID,
) -> dict[str, Any]:
    """5 aggregates over backtest_trades. Per ADR-0008 PF=None when undefined.

    Decimal/float boundary explicit: pnl + mdd are Decimal (NUMERIC sums per
    §5.3); wr + pf are float (proportions / dimensionless ratio).
    """
    rows = await conn.fetch(
        "SELECT realized_pnl FROM backtest_trades "
        "WHERE run_id = $1 AND status = 'closed' ORDER BY closed_at",
        run_id,
    )
    if not rows:
        return {"total_trades": 0, "wr": None, "pnl": "0", "pf": None, "mdd": "0"}

    pnls: list[Decimal] = [Decimal(r["realized_pnl"]) for r in rows]
    total_trades = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = round(wins / total_trades, 4)

    pnl: Decimal = sum(pnls, start=Decimal("0"))
    gross_wins: Decimal = sum((p for p in pnls if p > 0), start=Decimal("0"))
    gross_losses: Decimal = sum((-p for p in pnls if p < 0), start=Decimal("0"))

    # ADR-0008: PF=None for both edge cases.
    pf: float | None
    if gross_wins == 0 or gross_losses == 0:
        pf = None
    else:
        pf = round(float(gross_wins) / float(gross_losses), 4)

    cum, peak, mdd = Decimal("0"), Decimal("0"), Decimal("0")
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        drawdown = peak - cum
        if drawdown > mdd:
            mdd = drawdown

    return {
        "total_trades": total_trades,
        "wr": wr,
        "pnl": str(pnl),
        "pf": pf,
        "mdd": str(mdd),
    }


async def main(args: argparse.Namespace) -> int:
    """Composition root for backtest CLI."""
    # 1. Logger setup FIRST (CONCERN #4 fix).
    configure(level="INFO")
    trading_logger = get_logger("backtest", "trading")
    system_logger = get_logger("backtest", "system")
    audit_logger = get_logger("backtest", "audit")

    # 1a. ReplayClock — initial = args.from_at; advanced by paper + signal publisher.
    replay_clock = ReplayClock(initial=args.from_at)

    # 2. Load + override + hash bot config.
    plugin_registry = (
        load_plugin_registry(args.plugin_registry_path) if args.plugin_registry_path else None
    )
    config_yaml, config_hash, bot_config = _load_bot_config_with_overrides(
        config_path=args.config_path,
        overrides=args.overrides,
        plugin_registry=plugin_registry,
    )
    if args.overrides:
        system_logger.warning(
            "backtest.config_hash_diverged",
            reason=(
                "yaml.safe_dump round-trip is non-identity; hash differs from "
                "user YAML even for value-preserving overrides"
            ),
        )
    bot_id = BotId(args.bot)

    # 3. asyncpg pool.
    db_url = args.db_url or _require_env("DATABASE_URL")
    pool = await create_pool(db_url, application_name="backtest-cli")
    run_id: UUID | None = None
    try:
        # 4. Insert backtest_runs row (queued → running).
        run_name = args.name or (
            f"cli-{bot_id}-{args.from_at.isoformat()}-{args.to_at.isoformat()}"
        )
        async with pool.acquire() as conn:
            run_row = await insert_backtest_run(
                conn,
                name=run_name,
                bot_id=str(bot_id),
                config_yaml=config_yaml,
                config_hash=config_hash,
                date_range_start=args.from_at,
                date_range_end=args.to_at,
                started_at=datetime.now(UTC),
                notes=args.notes,
            )
            run_id = run_row.id
            await update_backtest_run_to_running(
                conn,
                run_id=run_id,
                started_at=datetime.now(UTC),
            )

        system_logger.info(
            "backtest.started",
            run_id=str(run_id),
            bot_id=str(bot_id),
            **{"from": args.from_at.isoformat(), "to": args.to_at.isoformat()},
        )

        # 5. ReplayBus + sources + paper exchange.
        bus = ReplayBus()
        ohlc_source = HistoricalOHLCSource(
            pool,
            symbols=bot_config.symbols,
            from_at=args.from_at,
            to_at=args.to_at,
            pace=args.pace,
            source=args.source,
        )
        signal_source = HistoricalSignalSource(
            pool,
            bot_id=bot_id,
            symbol_universe=bot_config.symbols,
            from_at=args.from_at,
            to_at=args.to_at,
        )
        paper = PaperExchange(
            seed_balance=_DEFAULT_SEED_BALANCE,
            slippage_model=_DEFAULT_SLIPPAGE_MODEL,
            slippage_params=_DEFAULT_SLIPPAGE_PARAMS,
            fee_rate=bot_config.execution.fee_rate,
            bot_id=bot_id,
            bus=bus,
            pool=pool,
            now_fn=replay_clock.now,
            mode="replay",
            historical_source=ohlc_source,
            replay_clock=replay_clock,
        )

        # 6. FeatureResolver (DB-fallback per OQ-5=A; ReplayBus.kv_get returns None).
        resolver = FeatureResolver(bus=bus, pool=pool, bound_logger=system_logger)

        # 7. Wire signal handler (signals.validated).
        signal_handler = make_signal_handler(
            bot_id=bot_id,
            bot_config=bot_config,
            resolver=resolver,
            pool=pool,
            bus=bus,
            trading_logger=trading_logger,
            system_logger=system_logger,
            audit_logger=audit_logger,
            now_fn=replay_clock.now,
            max_signal_age_seconds=bot_config.signals.ttl_seconds,
        )
        await bus.subscribe("signals.validated", signal_handler)

        # 8. Wire per-bot order request handler (orders.requests.<bot_id>).
        # Without this, strategy-engine's order publishes vanish into ReplayBus.
        position_lifecycle_tasks: dict[int, asyncio.Task[None]] = {}
        exec_settings = ExecutionSettings()  # type: ignore[call-arg]  # pydantic-settings env-fallback for database_url
        order_handler = make_per_bot_handler(
            bot_id=bot_id,
            adapter=paper,
            bus=bus,
            logger=system_logger,
            pool=pool,
            dedup_capacity=exec_settings.execution_orders_dedup_capacity,
            now_fn=replay_clock.now,
            fill_price_retry_attempts=exec_settings.execution_fill_price_retry_attempts,
            fill_price_retry_backoff_s=exec_settings.execution_fill_price_retry_backoff_s,
            position_lifecycle_tasks=position_lifecycle_tasks,
            position_poll_interval_s=exec_settings.position_poll_interval_s,
            position_poll_stale_ticks=exec_settings.position_poll_stale_ticks,
        )
        await bus.subscribe(subject_for_orders_request(bot_id), order_handler)

        # 9. ExecutionDispatcher + dispatcher task (consumes paper.stream_executions).
        dispatcher = ExecutionDispatcher(
            bot_id=bot_id,
            pool=pool,
            bus=bus,
            bound_logger=system_logger,
            capacity=exec_settings.dispatch_dedup_capacity,
            now_fn=replay_clock.now,
            adapter=paper,
            sub_account=str(bot_id),  # paper convention (Decision #8)
            closed_pnl_lock=asyncio.Lock(),
            # ADR-0006 D2: paper-mode get_closed_pnl_cumulative is synchronous SQL SUM.
            closed_pnl_post_close_sleep_s=0.0,
        )
        dispatcher_task = asyncio.create_task(
            run_dispatcher_for_bot(
                adapter=paper,
                dispatcher=dispatcher,
                bound_logger=system_logger,
            )
        )

        # 10. Drain coordination: gather signal+replay; bus.run_until_empty; cancel dispatcher.
        signal_publisher_task = asyncio.create_task(
            _publish_signals(signal_source, bus, bot_config, replay_clock),
        )
        replay_task = asyncio.create_task(paper.run_replay())
        try:
            await asyncio.gather(signal_publisher_task, replay_task)
            await bus.run_until_empty()
            dispatcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await dispatcher_task
        except Exception:
            dispatcher_task.cancel()
            raise

        # 11. SQL copy paper_trades → backtest_trades + summary + completion.
        async with pool.acquire() as conn, conn.transaction():
            trade_count = await copy_paper_trades_to_backtest(
                conn,
                run_id=run_id,
                bot_id=str(bot_id),
            )
            summary = await _compute_summary(conn, run_id=run_id)
            await update_backtest_run_completion(
                conn,
                run_id=run_id,
                status=BacktestStatus.COMPLETED,
                summary=summary,
                finished_at=datetime.now(UTC),
            )

        system_logger.info(
            "backtest.completed",
            run_id=str(run_id),
            trade_count=trade_count,
            summary=summary,
        )
        return 0

    except Exception as exc:
        if run_id is not None:
            async with pool.acquire() as conn:
                await update_backtest_run_completion(
                    conn,
                    run_id=run_id,
                    status=BacktestStatus.FAILED,
                    summary={"error": str(exc)[:500]},
                    finished_at=datetime.now(UTC),
                )
        system_logger.error("backtest.failed", error=str(exc), exc_info=True)
        return 1
    finally:
        await pool.close()


def _require_env(name: str) -> str:
    import os

    value = os.environ.get(name)
    if value is None:
        sys.stderr.write(f"{name} env var required (or pass --db-url for DATABASE_URL)\n")
        raise SystemExit(2)
    return value


def cli_main() -> int:
    """argparse entry; parse args, run async loop, return exit code."""
    parser = _build_parser()
    args = parser.parse_args()
    args.from_at = _parse_utc_datetime("from", args.from_at)
    args.to_at = _parse_utc_datetime("to", args.to_at)
    if args.to_at <= args.from_at:
        sys.stderr.write("--to must be > --from\n")
        return 2
    return asyncio.run(main(args))


if __name__ == "__main__":
    sys.exit(cli_main())
