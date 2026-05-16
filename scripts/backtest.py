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
from uuid import UUID, uuid4

import yaml
from prometheus_client import CollectorRegistry

from packages.bus import MessageEnvelope, ReplayBus
from packages.bus.schemas import SignalValidated
from packages.bus.schemas.orders import subject_for_orders_request
from packages.core import BotId, CorrelationId
from packages.core.replay_clock import ReplayClock
from packages.core.types import BacktestStatus
from packages.db.pool import create_pool
from packages.db.queries.analytics import (
    copy_paper_trades_to_backtest,
    count_common_signals_for_compare,
    insert_backtest_run,
    select_backtest_run_summary,
    select_diverging_trades_for_compare,
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
from services.execution.app.metrics import build_execution_metrics
from services.execution.app.placement import make_per_bot_handler
from services.strategy_engine.app.consumer import make_signal_handler
from services.strategy_engine.app.metrics import (
    build_registry as build_strategy_registry,
)
from services.strategy_engine.app.metrics import (
    build_strategy_engine_metrics,
)

if TYPE_CHECKING:
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
        description="Run a single-bot backtest replay or compare 2 runs (BRIEF §12.2:1949).",
    )
    # T-508: --compare mode flag (mutually exclusive with run-mode flags;
    # validated manually in cli_main since argparse mutually_exclusive_group
    # doesn't support multi-flag groups cleanly).
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("RUN_A_UUID", "RUN_B_UUID"),
        default=None,
        help="Compare 2 backtest runs by UUID (BRIEF §12.2:1969-1970)",
    )
    # Run-mode flags: NOT required at argparse level (T-508 dispatch logic).
    parser.add_argument("--bot", default=None, help="bot_id matching bots row")
    parser.add_argument(
        "--from",
        dest="from_at",
        default=None,
        help="ISO-8601 UTC start (e.g. 2026-04-01T00:00:00+00:00)",
    )
    parser.add_argument(
        "--to",
        dest="to_at",
        default=None,
        help="ISO-8601 UTC end (must be > --from; tzinfo must be UTC)",
    )
    parser.add_argument(
        "--config-path",
        dest="config_path",
        default=None,
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


def _parse_uuid(label: str, raw: str) -> UUID:
    """T-508 WG#2: validate UUID syntax; SystemExit(2) with stderr on invalid."""
    try:
        return UUID(raw)
    except ValueError as exc:
        sys.stderr.write(f"--compare {label} value {raw!r} is not valid UUID\n")
        raise SystemExit(2) from exc


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
        # 4. T-509 dispatch: external run_id (worker path) skips insert +
        # transition (worker already CLAIMED + transitioned atomically per
        # OQ-2=A). Else CLI path: create fresh row + transition to running.
        external_run_id = getattr(args, "run_id", None)
        if external_run_id is not None:
            run_id = external_run_id
            log_source = "t509_worker"
        else:
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
            log_source = "cli"

        system_logger.info(
            "backtest.started",
            run_id=str(run_id),
            bot_id=str(bot_id),
            source=log_source,
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

        # 7. Wire signal handler (signals.validated). T-526 cooldown gate is
        # short-circuited because backtest bot_config carries default-zero
        # risk: section (loader injects RiskSection() when block absent);
        # registry + metrics still constructed for type-safe kwarg pass.
        strategy_registry = build_strategy_registry()
        strategy_metrics = build_strategy_engine_metrics(strategy_registry)
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
            metrics=strategy_metrics,
        )
        await bus.subscribe("signals.validated", signal_handler)

        # 8. Wire per-bot order request handler (orders.requests.<bot_id>).
        # Without this, strategy-engine's order publishes vanish into ReplayBus.
        position_lifecycle_tasks: dict[int, asyncio.Task[None]] = {}
        exec_settings = ExecutionSettings()  # type: ignore[call-arg]  # pydantic-settings env-fallback for database_url
        # T-527b2b: registry + metrics constructed for type-safe kwarg pass
        # (mirror strategy_metrics:470-471). Throwaway CollectorRegistry —
        # backtest is offline replay, no Prometheus scrape endpoint.
        exec_metrics = build_execution_metrics(CollectorRegistry())
        order_handler = make_per_bot_handler(
            bot_id=bot_id,
            adapter=paper,
            # Paper adapter sub_account = bot_id-as-sub_account synonym
            # (Decision #8; what main.py _resolve_sub_account(paper) returns).
            sub_account=str(bot_id),
            metrics=exec_metrics,
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


# --- T-508 --compare mode helpers + main_compare -------------------------------


def _format_metric_delta(
    a: Any,
    b: Any,
    *,
    is_decimal: bool,
    is_proportion: bool = False,
) -> str:
    """Format delta column cell: signed delta or 'n/a' (PF=None per ADR-0008)."""
    if a is None or b is None:
        return "n/a"
    if is_decimal:
        delta_dec = Decimal(str(b)) - Decimal(str(a))
        sign = "+" if delta_dec >= 0 else ""
        return f"{sign}{delta_dec}"
    if is_proportion:
        delta_f = float(b) - float(a)
        sign = "+" if delta_f >= 0 else ""
        return f"{sign}{round(delta_f, 4):.4f}"
    # int (total_trades)
    delta_i = int(b) - int(a)
    sign = "+" if delta_i >= 0 else ""
    return f"{sign}{delta_i}"


def _format_metric_value(value: Any, *, is_proportion: bool = False) -> str:
    """Render summary value: '—' for None (PF), formatted for type otherwise."""
    if value is None:
        return "—"
    if is_proportion:
        return f"{round(float(value), 4):.4f}"
    return str(value)


def _format_aggregate_diff(summary_a: dict[str, Any], summary_b: dict[str, Any]) -> str:
    """T-508: 5-row aggregate metrics text-table (A / B / Δ).

    Per ADR-0008: PF=None → '—' value cell, 'n/a' delta cell.
    Per OQ-1=A text-only output.
    """
    rows = [
        (
            "total_trades",
            summary_a.get("total_trades", 0),
            summary_b.get("total_trades", 0),
            False,
            False,
        ),
        ("wr", summary_a.get("wr"), summary_b.get("wr"), False, True),
        ("pnl", summary_a.get("pnl", "0"), summary_b.get("pnl", "0"), True, False),
        ("pf", summary_a.get("pf"), summary_b.get("pf"), False, True),
        ("mdd", summary_a.get("mdd", "0"), summary_b.get("mdd", "0"), True, False),
    ]
    lines = ["Aggregate metrics:"]
    lines.append(f"  {'Metric':<14}  {'A':<12}  {'B':<12}  {'Δ (B-A)':<12}")
    lines.append(f"  {'-' * 14}  {'-' * 12}  {'-' * 12}  {'-' * 12}")
    for name, a, b, is_dec, is_prop in rows:
        a_cell = _format_metric_value(a, is_proportion=is_prop)
        b_cell = _format_metric_value(b, is_proportion=is_prop)
        delta_cell = _format_metric_delta(a, b, is_decimal=is_dec, is_proportion=is_prop)
        lines.append(f"  {name:<14}  {a_cell:<12}  {b_cell:<12}  {delta_cell:<12}")
    return "\n".join(lines) + "\n"


def _format_per_trade_diff(rows: list[Any], common_count: int) -> str:
    """T-508 WG#3: per-trade diff text-table.

    M=0 (no common signals) → "No common signals between run_A and run_B" line.
    M>0, N=0 (rows match) → "No diverging trades found." line.
    M>0, N>0 → header + table rows.
    """
    if common_count == 0:
        return "No common signals between run_A and run_B; per-trade diff not applicable.\n"
    n = len(rows)
    if n == 0:
        return "No diverging trades found.\n"
    lines = [f"Per-trade differences ({n} of {common_count} common signals diverged):"]
    lines.append(
        f"  {'signal_id':<10}  {'A.close_reason':<15}  {'A.realized_pnl':<15}  "
        f"{'B.close_reason':<15}  {'B.realized_pnl':<15}"
    )
    lines.append(f"  {'-' * 10}  {'-' * 15}  {'-' * 15}  {'-' * 15}  {'-' * 15}")
    for r in rows:
        a_reason = r.a_close_reason or ""
        b_reason = r.b_close_reason or ""
        a_pnl = str(r.a_realized_pnl) if r.a_realized_pnl is not None else ""
        b_pnl = str(r.b_realized_pnl) if r.b_realized_pnl is not None else ""
        lines.append(
            f"  {r.signal_id:<10}  {a_reason:<15}  {a_pnl:<15}  {b_reason:<15}  {b_pnl:<15}"
        )
    return "\n".join(lines) + "\n"


async def main_compare(args: argparse.Namespace) -> int:
    """T-508 composition root for --compare mode (read-only)."""
    configure(level="INFO")
    system_logger = get_logger("backtest", "system")

    # WG#2: parse UUIDs BEFORE opening pool (syntax-only check).
    run_a_id = _parse_uuid("run_A_uuid", args.compare[0])
    run_b_id = _parse_uuid("run_B_uuid", args.compare[1])

    db_url = args.db_url or _require_env("DATABASE_URL")
    pool = await create_pool(db_url, application_name="backtest-cli-compare")
    try:
        async with pool.acquire() as conn:
            summary_a = await select_backtest_run_summary(conn, run_id=run_a_id)
            summary_b = await select_backtest_run_summary(conn, run_id=run_b_id)
            if summary_a is None:
                sys.stderr.write(f"run_id {run_a_id} not found in backtest_runs\n")
                return 1
            if summary_b is None:
                sys.stderr.write(f"run_id {run_b_id} not found in backtest_runs\n")
                return 1
            diverging = await select_diverging_trades_for_compare(
                conn,
                run_a=run_a_id,
                run_b=run_b_id,
            )
            common_count = await count_common_signals_for_compare(
                conn,
                run_a=run_a_id,
                run_b=run_b_id,
            )

        sys.stdout.write(f"Backtest comparison: A={str(run_a_id)[:8]} vs B={str(run_b_id)[:8]}\n")
        sys.stdout.write("=" * 64 + "\n\n")
        sys.stdout.write(_format_aggregate_diff(summary_a, summary_b))
        sys.stdout.write("\n")
        sys.stdout.write(_format_per_trade_diff(diverging, common_count))
        return 0

    except Exception as exc:
        system_logger.error("backtest.compare_failed", error=str(exc), exc_info=True)
        return 1
    finally:
        await pool.close()


def cli_main() -> int:
    """argparse entry; parse args, dispatch run-mode or compare-mode."""
    parser = _build_parser()
    args = parser.parse_args()
    # T-508 dispatch.
    if args.compare:
        # WG#1: hard-fail on mode conflict (compare + any run-mode flag set).
        run_mode_flags = (args.bot, args.from_at, args.to_at, args.config_path)
        if any(f is not None for f in run_mode_flags):
            sys.stderr.write(
                "--compare is mutually exclusive with --bot/--from/--to/"
                "--config-path; pick one mode\n"
            )
            return 2
        # WG#1: same UUID twice → reject.
        if args.compare[0] == args.compare[1]:
            sys.stderr.write("--compare run_A_uuid must differ from run_B_uuid\n")
            return 2
        return asyncio.run(main_compare(args))
    # Run-mode: validate all 4 required flags.
    if not (args.bot and args.from_at and args.to_at and args.config_path):
        sys.stderr.write(
            "Run mode requires --bot, --from, --to, --config-path "
            "(or use --compare for comparison mode)\n"
        )
        return 2
    args.from_at = _parse_utc_datetime("from", args.from_at)
    args.to_at = _parse_utc_datetime("to", args.to_at)
    if args.to_at <= args.from_at:
        sys.stderr.write("--to must be > --from\n")
        return 2
    return asyncio.run(main(args))


if __name__ == "__main__":
    sys.exit(cli_main())
