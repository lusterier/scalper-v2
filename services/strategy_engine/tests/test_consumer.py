"""§N5 unit tests for :mod:`services.strategy_engine.app.consumer` (T-310b).

Tests close §9.4 main loop steps 3a-3h verbatim per BRIEF + WG#1..#5 pins:

* H-008 verbatim hazard pin (BRIEF §20:2661 ``test_expired_signal_is_dropped_at_strategy_engine``)
* TTL strict ``<`` per brief §9.4:1536 verbatim — no ``<=`` drift
* Publish-after-persist ordering (T-200 Q2): scoring_evaluations INSERT BEFORE bus.publish
* Single ``now = now_fn()`` snapshot per handler invocation (WG#1; grep count = 1)
* CLOSE action explicit log + return BEFORE any decision dispatch (WG#5)
* §N2 log-stream routing pinned via per-test bound_logger argument verification
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from packages.bus import MessageEnvelope
from packages.bus.errors import PublishError
from packages.bus.schemas import OrderRequest, SignalRejected, SignalValidated
from packages.scoring import (
    BotConfig,
    ExchangeSection,
    ExecutionSection,
    RuleResult,
    ScoringConfig,
    ScoringResult,
    SignalsSection,
)
from services.strategy_engine.app.consumer import make_signal_handler

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest


_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _bot_config() -> BotConfig:
    return BotConfig(
        bot_id="alpha",
        symbols=["BTCUSDT"],
        exchange=ExchangeSection(
            mode="paper",
            account="sub_alpha",
            api_key_env="K",
            api_secret_env="S",
        ),
        signals=SignalsSection(),
        execution=ExecutionSection(
            qty=Decimal("0.001"),
            leverage=20,
            sl_pct=Decimal("0.01"),
            tp_pct=Decimal("0.01"),
            tp_qty_pct=Decimal("0.5"),
            be_trigger=Decimal("0.005"),
            be_sl_level=Decimal("0.003"),
            trail_pct=Decimal("0.005"),
            fee_rate=Decimal("0.00055"),
        ),
        scoring=ScoringConfig(trigger_threshold=1.0, rules=[]),
    )


def _signal(
    *,
    symbol: str = "BTCUSDT",
    action: str = "LONG",
    expires_at: datetime | None = None,
    idempotency_key: str = "key-1",
) -> SignalValidated:
    return SignalValidated(
        source="webhook",
        idempotency_key=idempotency_key,
        received_at=_FIXED_NOW - timedelta(seconds=10),
        symbol=symbol,
        original_symbol=symbol,
        action=action,  # type: ignore[arg-type]
        expires_at=expires_at if expires_at is not None else _FIXED_NOW + timedelta(seconds=60),
        payload={},
    )


def _envelope(signal: SignalValidated, *, correlation_id: str = "corr-1") -> MessageEnvelope:
    return MessageEnvelope(
        correlation_id=correlation_id,  # type: ignore[arg-type]
        publisher="signal-gateway",
        payload=signal.model_dump(mode="json"),
    )


def _scoring_result(
    *,
    decision: str = "execute",
    total_score: float = 1.5,
    threshold: float = 1.0,
    reason: str | None = None,
) -> ScoringResult:
    return ScoringResult(
        decision=decision,  # type: ignore[arg-type]
        reason=reason,
        total_score=total_score,
        threshold=threshold,
        rule_results=[
            RuleResult(name="r1", weight=1.0, applied_weight=1.5, result="True", error=None),
        ],
        feature_snapshot={"ind.btcusdt.15m.ema_20": {"value_num": "60000"}},
        config_version=1,
    )


def _mock_pool_with_signal_id(signal_id: int | None) -> MagicMock:
    pool = MagicMock()
    fake_conn = MagicMock()
    fake_conn.fetchrow = AsyncMock(
        return_value={"id": signal_id} if signal_id is not None else None,
    )
    fake_conn.execute = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _build_handler(
    *,
    bot_config: BotConfig | None = None,
    pool: MagicMock | None = None,
    bus: MagicMock | None = None,
    trading_logger: MagicMock | None = None,
    system_logger: MagicMock | None = None,
    audit_logger: MagicMock | None = None,
    evaluate_result: ScoringResult | None = None,
    evaluate_raises: Exception | None = None,
    now: datetime = _FIXED_NOW,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[
    Callable[[MessageEnvelope], Awaitable[None]],
    dict[str, Any],
]:
    """Build a handler with default mocks; return (handler, captures dict)."""
    bc = bot_config or _bot_config()
    captures: dict[str, Any] = {
        "pool": pool or _mock_pool_with_signal_id(42),
        "bus": bus or _mock_bus(),
        "trading": trading_logger or MagicMock(),
        "system": system_logger or MagicMock(),
        "audit": audit_logger or MagicMock(),
    }
    if monkeypatch is not None:
        if evaluate_raises is not None:

            async def _evaluate_fail(**_kwargs: Any) -> ScoringResult:
                raise evaluate_raises

            monkeypatch.setattr("services.strategy_engine.app.consumer.evaluate", _evaluate_fail)
        else:
            res = evaluate_result or _scoring_result()

            async def _evaluate_ok(**_kwargs: Any) -> ScoringResult:
                return res

            monkeypatch.setattr("services.strategy_engine.app.consumer.evaluate", _evaluate_ok)

        # T-513a: stub select_latest_close so rejection branch _resolve_virtual_entry
        # gets a deterministic Decimal back (default Decimal("65000")). Tests that
        # need cold-start path (None) or PG-error path patch this further inline.
        async def _select_latest_close_default(_conn: Any, **_kwargs: Any) -> Decimal | None:
            return Decimal("65000")

        monkeypatch.setattr(
            "services.strategy_engine.app.consumer.select_latest_close",
            _select_latest_close_default,
        )

    resolver = MagicMock()
    handler = make_signal_handler(
        bot_id="alpha",  # type: ignore[arg-type]
        bot_config=bc,
        resolver=resolver,
        pool=captures["pool"],
        bus=captures["bus"],
        trading_logger=captures["trading"],
        system_logger=captures["system"],
        audit_logger=captures["audit"],
        now_fn=lambda: now,
        max_signal_age_seconds=600,
        metrics=captures.get("metrics") or MagicMock(),
    )
    return handler, captures


def _mock_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


# region: §9.4 step 3a — TTL ------------------------------------------------


async def test_expired_signal_is_dropped_at_strategy_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-008 verbatim hazard pin per BRIEF §20:2661 (test name)."""
    expired = _signal(expires_at=_FIXED_NOW - timedelta(seconds=1))
    handler, caps = _build_handler(monkeypatch=monkeypatch)
    await handler(_envelope(expired))
    # No DB acquire, no publish.
    caps["pool"].acquire.assert_not_called()
    caps["bus"].publish.assert_not_called()
    # signal_expired logged on trading.log via trading_logger.
    caps["trading"].info.assert_called_once()
    assert caps["trading"].info.call_args.args[0] == "signal_expired"


# region: §9.4 step 3b — symbol filter --------------------------------------


async def test_signal_outside_universe_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """signal.symbol not in bot_config.symbols → log + return."""
    signal = _signal(symbol="ETHUSDT")  # bot_config.symbols == ["BTCUSDT"]
    handler, caps = _build_handler(monkeypatch=monkeypatch)
    await handler(_envelope(signal))
    caps["pool"].acquire.assert_not_called()
    caps["bus"].publish.assert_not_called()
    caps["trading"].info.assert_called_once()
    assert caps["trading"].info.call_args.args[0] == "signal_outside_universe"


# region: §9.4 step 3e/3g — execute/passthrough → OrderRequest --------------


async def test_decision_execute_publishes_order_request_with_full_field_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: subject orders.requests.alpha + full ExecutionSection mapping."""
    signal = _signal(action="LONG")
    handler, caps = _build_handler(
        evaluate_result=_scoring_result(decision="execute"),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(signal))
    caps["bus"].publish.assert_awaited_once()
    subject, env = caps["bus"].publish.await_args.args
    assert subject == "orders.requests.alpha"
    request = OrderRequest.model_validate(env.payload)
    assert request.bot_id == "alpha"
    assert request.signal_id == 42
    assert request.symbol == "BTCUSDT"
    assert request.side == "buy"  # LONG → buy
    assert request.qty == Decimal("0.001")
    assert request.leverage == 20
    assert request.sl_pct == Decimal("0.01")
    assert request.tp_qty_pct == Decimal("0.5")
    assert request.exchange_mode == "paper"


async def test_decision_passthrough_publishes_order_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """passthrough mode → same path as execute per §9.4:1542 verbatim."""
    handler, caps = _build_handler(
        evaluate_result=_scoring_result(decision="passthrough"),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    caps["bus"].publish.assert_awaited_once()
    subject, _ = caps["bus"].publish.await_args.args
    assert subject == "orders.requests.alpha"


# region: §9.4 step 3f — reject → SignalRejected ----------------------------


async def test_decision_reject_publishes_signal_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decision==reject → signals.rejected.<bot_id> with T-310a SignalRejected schema.

    T-513a / BRIEF §13.5: rejection branch ALSO publishes ShadowRejectedStartPayload
    on `shadow.rejected.start.<bot_id>` topic (paralelne); test filters publish
    calls to verify ONLY the SignalRejected envelope here.
    """
    handler, caps = _build_handler(
        evaluate_result=_scoring_result(
            decision="reject", reason="score_below_threshold", total_score=0.5
        ),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    publish_calls = caps["bus"].publish.await_args_list
    # T-513a: 2 publishes — signals.rejected.alpha + shadow.rejected.start.alpha.
    rejected_calls = [c for c in publish_calls if c.args[0] == "signals.rejected.alpha"]
    assert len(rejected_calls) == 1
    subject, env = rejected_calls[0].args
    assert subject == "signals.rejected.alpha"
    rejection = SignalRejected.model_validate(env.payload)
    assert rejection.bot_id == "alpha"
    assert rejection.signal_id == 42
    assert rejection.reason == "score_below_threshold"
    assert rejection.total_score == 0.5
    assert rejection.threshold == 1.0
    assert rejection.rejected_at == _FIXED_NOW


async def test_decision_short_action_maps_to_sell_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SHORT → side='sell'."""
    handler, caps = _build_handler(monkeypatch=monkeypatch)
    await handler(_envelope(_signal(action="SHORT")))
    request = OrderRequest.model_validate(caps["bus"].publish.await_args.args[1].payload)
    assert request.side == "sell"


async def test_decision_close_action_logged_and_skipped_in_v1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#5: CLOSE → log consumer.close_action_unsupported_v1 + return; no DB / publish."""
    handler, caps = _build_handler(monkeypatch=monkeypatch)
    await handler(_envelope(_signal(action="CLOSE")))
    caps["pool"].acquire.assert_not_called()
    caps["bus"].publish.assert_not_called()
    caps["trading"].info.assert_called_once()
    assert caps["trading"].info.call_args.args[0] == "consumer.close_action_unsupported_v1"


# region: §9.4 step 3h — publish-after-persist ordering ---------------------


async def test_scoring_evaluations_write_runs_before_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#3: insert_scoring_evaluation called BEFORE bus.publish (T-200 Q2)."""
    call_log: list[str] = []
    pool = _mock_pool_with_signal_id(42)

    async def _execute_recording(*_args: Any, **_kwargs: Any) -> None:
        call_log.append("db_insert")

    pool.acquire.return_value.__aenter__.return_value.execute = _execute_recording

    bus = MagicMock()

    async def _publish_recording(*_args: Any, **_kwargs: Any) -> None:
        call_log.append("bus_publish")

    bus.publish = _publish_recording

    handler, _ = _build_handler(pool=pool, bus=bus, monkeypatch=monkeypatch)
    await handler(_envelope(_signal()))
    assert call_log == ["db_insert", "bus_publish"]


# region: error paths -------------------------------------------------------


async def test_signal_id_unresolved_is_logged_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-310a lookup returns None → log signal_id_unresolved + return; no INSERT, no publish."""
    pool = _mock_pool_with_signal_id(None)
    handler, caps = _build_handler(pool=pool, monkeypatch=monkeypatch)
    await handler(_envelope(_signal()))
    caps["bus"].publish.assert_not_called()
    caps["trading"].info.assert_called_once()
    assert caps["trading"].info.call_args.args[0] == "signal_id_unresolved"


async def test_evaluator_crash_is_logged_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evaluate(...) raises → log scoring_evaluator_crashed + return; no INSERT, no publish."""
    handler, caps = _build_handler(
        evaluate_raises=RuntimeError("evaluator bug"),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    caps["bus"].publish.assert_not_called()
    caps["system"].error.assert_called_once()
    assert caps["system"].error.call_args.args[0] == "scoring_evaluator_crashed"


async def test_db_insert_failure_is_logged_no_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """insert_scoring_evaluation raises → log + return; no publish (publish-after-persist)."""
    pool = _mock_pool_with_signal_id(42)
    pool.acquire.return_value.__aenter__.return_value.execute = AsyncMock(
        side_effect=RuntimeError("db down"),
    )
    handler, caps = _build_handler(pool=pool, monkeypatch=monkeypatch)
    await handler(_envelope(_signal()))
    caps["bus"].publish.assert_not_called()
    caps["system"].error.assert_called_once()
    assert caps["system"].error.call_args.args[0] == "scoring_evaluations_insert_failed"


async def test_publish_failure_after_db_commit_logs_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish raises after DB INSERT → log via audit + system loggers (dual-write)."""
    bus = _mock_bus()
    bus.publish = AsyncMock(side_effect=PublishError("nats down"))
    handler, caps = _build_handler(bus=bus, monkeypatch=monkeypatch)
    await handler(_envelope(_signal()))
    # Audit + system both record the failure (WG#4 dual-write).
    caps["audit"].error.assert_called_once()
    caps["system"].error.assert_called_once()
    assert caps["audit"].error.call_args.args[0] == "orders_request_publish_failed"
    assert caps["system"].error.call_args.args[0] == "orders_request_publish_failed"


async def test_consumer_uses_bot_config_execution_section_for_order_request_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-310a ExecutionSection consumed: every Decimal field flows to OrderRequest verbatim."""
    bc = _bot_config()
    handler, caps = _build_handler(bot_config=bc, monkeypatch=monkeypatch)
    await handler(_envelope(_signal()))
    request = OrderRequest.model_validate(caps["bus"].publish.await_args.args[1].payload)
    assert request.qty == bc.execution.qty
    assert request.leverage == bc.execution.leverage
    assert request.sl_pct == bc.execution.sl_pct
    assert request.tp_pct == bc.execution.tp_pct
    assert request.tp_qty_pct == bc.execution.tp_qty_pct
    assert request.be_trigger == bc.execution.be_trigger
    assert request.be_sl_level == bc.execution.be_sl_level
    assert request.trail_pct == bc.execution.trail_pct
    assert request.exchange_mode == bc.exchange.mode


async def test_publish_order_request_empty_shadow_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-511b2 / ADR-0010: bot_config.shadow=None → OrderRequest.shadow_variants=[] +
    shadow_max_duration_hours=None."""
    bc = _bot_config()
    assert bc.shadow is None  # default — no shadow block in fixture
    handler, caps = _build_handler(bot_config=bc, monkeypatch=monkeypatch)
    await handler(_envelope(_signal()))
    request = OrderRequest.model_validate(caps["bus"].publish.await_args.args[1].payload)
    assert request.shadow_variants == []
    assert request.shadow_max_duration_hours is None


async def test_publish_order_request_populates_shadow_fields_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-511b2 / ADR-0010: bot_config.shadow.enabled=True with 2 variants → 2 VariantSpec
    entries on OrderRequest + shadow_max_duration_hours = Decimal("4")."""
    from packages.scoring.types import ShadowConfig, ShadowVariant

    bc = _bot_config().model_copy(
        update={
            "shadow": ShadowConfig(
                enabled=True,
                variants=[
                    ShadowVariant(name="aggressive", overrides={"be_trigger": Decimal("0.003")}),
                    ShadowVariant(name="conservative", overrides={"sl_pct": Decimal("0.01")}),
                ],
                max_duration_hours=4.0,
            ),
        },
    )
    handler, caps = _build_handler(bot_config=bc, monkeypatch=monkeypatch)
    await handler(_envelope(_signal()))
    request = OrderRequest.model_validate(caps["bus"].publish.await_args.args[1].payload)
    assert len(request.shadow_variants) == 2
    assert request.shadow_variants[0].name == "aggressive"
    assert request.shadow_variants[0].overrides["be_trigger"] == Decimal("0.003")
    assert request.shadow_variants[1].name == "conservative"
    # Float→Decimal cast via Decimal(str(value)) per WG#5.3 idiom.
    assert request.shadow_max_duration_hours == Decimal("4.0")


async def test_invalid_envelope_payload_is_logged_and_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Envelope payload not parseable as SignalValidated → log + return."""
    bad_envelope = MessageEnvelope(
        correlation_id="corr-1",  # type: ignore[arg-type]
        publisher="signal-gateway",
        payload={"not": "a signal"},
    )
    handler, caps = _build_handler(monkeypatch=monkeypatch)
    await handler(bad_envelope)
    caps["pool"].acquire.assert_not_called()
    caps["bus"].publish.assert_not_called()
    caps["system"].error.assert_called_once()
    assert caps["system"].error.call_args.args[0] == "consumer.signal_validated_validation_failed"


# ---------------------------------------------------------------------------
# T-513a / BRIEF §13.5 — rejection branch publishes ShadowRejectedStartPayload
# ---------------------------------------------------------------------------


async def test_decision_reject_publishes_shadow_rejected_start_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejection branch ALSO publishes shadow.rejected.start.<bot_id> with full envelope.

    Verify subject + payload fields (signal_id, bot_id, symbol, action, virtual_entry_price,
    sl/tp/be thresholds, rejected_at) match BotConfig values + signal context.
    """
    from packages.bus.payloads import ShadowRejectedStartPayload

    handler, caps = _build_handler(
        evaluate_result=_scoring_result(
            decision="reject", reason="score_below_threshold", total_score=0.5
        ),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    publish_calls = caps["bus"].publish.await_args_list
    shadow_rejected_calls = [c for c in publish_calls if c.args[0] == "shadow.rejected.start.alpha"]
    assert len(shadow_rejected_calls) == 1
    subject, env = shadow_rejected_calls[0].args
    assert subject == "shadow.rejected.start.alpha"
    payload = ShadowRejectedStartPayload.model_validate(env.payload)
    assert payload.signal_id == 42
    assert payload.bot_id == "alpha"
    assert payload.symbol == "BTCUSDT"
    assert payload.action == "LONG"
    assert payload.virtual_entry_price == Decimal("65000")
    assert payload.rejected_at == _FIXED_NOW


async def test_decision_reject_shadow_rejected_carries_botconfig_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ShadowRejectedStartPayload thresholds copy from BotConfig.execution verbatim."""
    from packages.bus.payloads import ShadowRejectedStartPayload

    handler, caps = _build_handler(
        evaluate_result=_scoring_result(decision="reject", reason="x", total_score=0.5),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    publish_calls = caps["bus"].publish.await_args_list
    sr_calls = [c for c in publish_calls if c.args[0] == "shadow.rejected.start.alpha"]
    assert len(sr_calls) == 1
    payload = ShadowRejectedStartPayload.model_validate(sr_calls[0].args[1].payload)
    # Mirror _bot_config() execution section.
    assert payload.sl_pct == Decimal("0.01")
    assert payload.tp_pct == Decimal("0.01")
    assert payload.be_trigger == Decimal("0.005")
    assert payload.be_sl_level == Decimal("0.003")


async def test_decision_reject_db_error_resolves_zero_virtual_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When :func:`select_latest_close` raises asyncpg.PostgresError, ``_resolve_virtual_entry``
    swallows the error + returns ``Decimal('0')``; rejection branch still publishes
    ShadowRejectedStartPayload (entry==0 — worker handles defensive skip)."""
    import asyncpg

    from packages.bus.payloads import ShadowRejectedStartPayload

    handler, caps = _build_handler(
        evaluate_result=_scoring_result(decision="reject", reason="x", total_score=0.5),
        monkeypatch=monkeypatch,
    )

    async def _select_latest_close_raises(_conn: Any, **_kwargs: Any) -> Decimal | None:
        raise asyncpg.PostgresError("simulated DB outage")

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.select_latest_close",
        _select_latest_close_raises,
    )

    await handler(_envelope(_signal()))
    publish_calls = caps["bus"].publish.await_args_list
    sr_calls = [c for c in publish_calls if c.args[0] == "shadow.rejected.start.alpha"]
    assert len(sr_calls) == 1
    payload = ShadowRejectedStartPayload.model_validate(sr_calls[0].args[1].payload)
    assert payload.virtual_entry_price == Decimal("0")


# region: T-526 pre-scoring cooldown gate -----------------------------------


async def test_signal_blocked_by_cooldown_skips_db_and_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-526 WG#5: cooldown_active → signal_blocked_cooldown log + Prom counter inc +
    NO scoring_evaluations write + NO orders.requests publish + NO signals.rejected
    publish + NO shadow rejected start. Pin both "what fires" + "what does NOT" sides.
    """
    from services.strategy_engine.app.cooldown_gate import CooldownDecision

    cooldown_until = _FIXED_NOW + timedelta(minutes=55)
    last_loss_at = _FIXED_NOW - timedelta(minutes=5)
    active = CooldownDecision(
        active=True,
        reason="cooldown_after_streak",
        cooldown_until=cooldown_until,
        streak_count=3,
        last_loss_at=last_loss_at,
    )

    async def _stub_check_cooldown(**_kwargs: Any) -> CooldownDecision:
        return active

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.check_cooldown",
        _stub_check_cooldown,
    )

    # signal_id resolve must NOT be called when cooldown active — stub fails loudly
    # if consumer reaches step 3c.
    async def _select_signal_id_must_not_run(*_args: Any, **_kwargs: Any) -> int:
        raise AssertionError("step 3c reached despite cooldown active")

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.select_signal_id_by_idempotency_key",
        _select_signal_id_must_not_run,
    )

    metrics = MagicMock()
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("pool.acquire must not run"))
    bus = _mock_bus()
    captured_trading = MagicMock()

    handler = make_signal_handler(
        bot_id="alpha",  # type: ignore[arg-type]
        bot_config=_bot_config(),
        resolver=MagicMock(),
        pool=pool,
        bus=bus,
        trading_logger=captured_trading,
        system_logger=MagicMock(),
        audit_logger=MagicMock(),
        now_fn=lambda: _FIXED_NOW,
        max_signal_age_seconds=600,
        metrics=metrics,
    )
    await handler(_envelope(_signal()))

    # Pin: signal_blocked_cooldown logged on trading.log with EXACT field set per WG#5.
    captured_trading.info.assert_called_once()
    call = captured_trading.info.call_args
    assert call.args[0] == "signal_blocked_cooldown"
    kwargs = call.kwargs
    assert kwargs["bot_id"] == "alpha"
    assert kwargs["idempotency_key"] == "key-1"
    assert kwargs["symbol"] == "BTCUSDT"
    assert kwargs["reason"] == "cooldown_after_streak"
    assert kwargs["cooldown_until"] == cooldown_until.isoformat()
    assert kwargs["streak_count"] == 3
    assert kwargs["last_loss_at"] == last_loss_at.isoformat()

    # Pin: Prom counter incremented with bot_id + reason labels.
    metrics.signals_blocked_cooldown.labels.assert_called_once_with(
        bot_id="alpha", reason="cooldown_after_streak"
    )
    metrics.signals_blocked_cooldown.labels.return_value.inc.assert_called_once_with()

    # Pin: NO DB writes + NO bus publishes + NO step-3c signal_id lookup.
    bus.publish.assert_not_awaited()
    pool.acquire.assert_not_called()


async def test_cooldown_inactive_passes_through_to_step_3c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When cooldown gate returns inactive, consumer proceeds to step 3c normally."""
    from services.strategy_engine.app.cooldown_gate import CooldownDecision

    async def _stub_check_cooldown(**_kwargs: Any) -> CooldownDecision:
        return CooldownDecision(
            active=False,
            reason=None,
            cooldown_until=None,
            streak_count=0,
            last_loss_at=None,
        )

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.check_cooldown",
        _stub_check_cooldown,
    )

    handler, caps = _build_handler(
        evaluate_result=_scoring_result(decision="execute"),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    # Step 3c..3h ran → bus.publish on orders.requests fired.
    caps["bus"].publish.assert_awaited()


# region: T-524 pre-scoring concurrent-caps gate ----------------------------


async def test_signal_blocked_by_caps_skips_db_and_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-524 WG#5: caps blocked → signal_blocked_caps log + Prom counter inc +
    NO scoring_evaluations write + NO orders.requests / signals.rejected publish.
    Dual-side pin (what fires + what does NOT). Cooldown gate short-circuits
    inactive via all-zero _bot_config RiskSection (no stub needed)."""
    from services.strategy_engine.app.concurrent_caps_gate import CapsDecision

    blocked = CapsDecision(
        blocked=True,
        reason="max_open_trades_per_bot",
        current_count=3,
        cap_limit=3,
    )

    async def _stub_check_caps(**_kwargs: Any) -> CapsDecision:
        return blocked

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.check_concurrent_caps",
        _stub_check_caps,
    )

    async def _signal_id_must_not_run(*_a: Any, **_k: Any) -> int:
        raise AssertionError("step 3c reached despite caps blocked")

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.select_signal_id_by_idempotency_key",
        _signal_id_must_not_run,
    )

    metrics = MagicMock()
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("pool.acquire must not run"))
    bus = _mock_bus()
    captured_trading = MagicMock()

    handler = make_signal_handler(
        bot_id="alpha",  # type: ignore[arg-type]
        bot_config=_bot_config(),
        resolver=MagicMock(),
        pool=pool,
        bus=bus,
        trading_logger=captured_trading,
        system_logger=MagicMock(),
        audit_logger=MagicMock(),
        now_fn=lambda: _FIXED_NOW,
        max_signal_age_seconds=600,
        metrics=metrics,
    )
    await handler(_envelope(_signal()))

    # Pin: signal_blocked_caps logged with EXACT 6-kwarg field set per WG#5.
    captured_trading.info.assert_called_once()
    call = captured_trading.info.call_args
    assert call.args[0] == "signal_blocked_caps"
    kw = call.kwargs
    assert kw["bot_id"] == "alpha"
    assert kw["idempotency_key"] == "key-1"
    assert kw["symbol"] == "BTCUSDT"
    assert kw["reason"] == "max_open_trades_per_bot"
    assert kw["current_count"] == 3
    assert kw["cap_limit"] == 3

    # Pin: Prom counter incremented with bot_id + reason labels.
    metrics.signals_blocked_caps.labels.assert_called_once_with(
        bot_id="alpha", reason="max_open_trades_per_bot"
    )
    metrics.signals_blocked_caps.labels.return_value.inc.assert_called_once_with()

    # Pin: NO DB writes + NO bus publishes + NO step-3c signal_id lookup.
    bus.publish.assert_not_awaited()
    pool.acquire.assert_not_called()


async def test_caps_not_blocked_passes_through_to_step_3c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caps gate not blocked → consumer proceeds to 3c..3h normally."""
    from services.strategy_engine.app.concurrent_caps_gate import CapsDecision

    async def _stub_check_caps(**_kwargs: Any) -> CapsDecision:
        return CapsDecision(blocked=False, reason=None, current_count=None, cap_limit=None)

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.check_concurrent_caps",
        _stub_check_caps,
    )

    handler, caps = _build_handler(
        evaluate_result=_scoring_result(decision="execute"),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    caps["bus"].publish.assert_awaited()


# region: T-525a2 pre-scoring daily-loss kill-switch gate --------------------


async def test_signal_blocked_by_loss_limit_skips_db_and_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-525a2 WG#7: loss-limit blocked → signal_blocked_loss_limit log
    (Decimal-as-str) + Prom inc + NO scoring_evaluations / orders.requests /
    signals.rejected. Dual-side pin. Cooldown + caps short-circuit inactive
    via all-zero _bot_config RiskSection (no stub needed for those)."""
    from decimal import Decimal

    from services.strategy_engine.app.loss_limit_gate import LossLimitDecision

    blocked = LossLimitDecision(
        blocked=True,
        reason="daily_loss_limit",
        cumulative_loss_usd=Decimal("-105.0000"),
        limit_usd=Decimal("100"),
    )

    async def _stub_check_loss_limit(**_kwargs: Any) -> LossLimitDecision:
        return blocked

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.check_daily_loss_limit",
        _stub_check_loss_limit,
    )

    async def _signal_id_must_not_run(*_a: Any, **_k: Any) -> int:
        raise AssertionError("step 3c reached despite loss-limit blocked")

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.select_signal_id_by_idempotency_key",
        _signal_id_must_not_run,
    )

    metrics = MagicMock()
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=AssertionError("pool.acquire must not run"))
    bus = _mock_bus()
    captured_trading = MagicMock()

    handler = make_signal_handler(
        bot_id="alpha",  # type: ignore[arg-type]
        bot_config=_bot_config(),
        resolver=MagicMock(),
        pool=pool,
        bus=bus,
        trading_logger=captured_trading,
        system_logger=MagicMock(),
        audit_logger=MagicMock(),
        now_fn=lambda: _FIXED_NOW,
        max_signal_age_seconds=600,
        metrics=metrics,
    )
    await handler(_envelope(_signal()))

    captured_trading.info.assert_called_once()
    call = captured_trading.info.call_args
    assert call.args[0] == "signal_blocked_loss_limit"
    kw = call.kwargs
    assert kw["bot_id"] == "alpha"
    assert kw["idempotency_key"] == "key-1"
    assert kw["symbol"] == "BTCUSDT"
    assert kw["reason"] == "daily_loss_limit"
    # Decimal logged via str() (exact).
    assert kw["cumulative_loss_usd"] == "-105.0000"
    assert kw["limit_usd"] == "100"

    metrics.signals_blocked_loss_limit.labels.assert_called_once_with(
        bot_id="alpha", reason="daily_loss_limit"
    )
    metrics.signals_blocked_loss_limit.labels.return_value.inc.assert_called_once_with()

    bus.publish.assert_not_awaited()
    pool.acquire.assert_not_called()


async def test_loss_limit_not_blocked_passes_through_to_step_3c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loss-limit gate not blocked → consumer proceeds to 3c..3h normally."""
    from services.strategy_engine.app.loss_limit_gate import LossLimitDecision

    async def _stub(**_kwargs: Any) -> LossLimitDecision:
        return LossLimitDecision(
            blocked=False, reason=None, cumulative_loss_usd=None, limit_usd=None
        )

    monkeypatch.setattr("services.strategy_engine.app.consumer.check_daily_loss_limit", _stub)
    handler, caps = _build_handler(
        evaluate_result=_scoring_result(decision="execute"),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    caps["bus"].publish.assert_awaited()


async def test_gate_chain_order_cooldown_caps_loss_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WG#8: gates run in order cooldown → caps → loss-limit (3rd sibling last)."""
    from services.strategy_engine.app.concurrent_caps_gate import CapsDecision
    from services.strategy_engine.app.cooldown_gate import CooldownDecision
    from services.strategy_engine.app.loss_limit_gate import LossLimitDecision

    order: list[str] = []

    async def _cd(**_k: Any) -> CooldownDecision:
        order.append("cooldown")
        return CooldownDecision(
            active=False, reason=None, cooldown_until=None, streak_count=0, last_loss_at=None
        )

    async def _caps(**_k: Any) -> CapsDecision:
        order.append("caps")
        return CapsDecision(blocked=False, reason=None, current_count=None, cap_limit=None)

    async def _ll(**_k: Any) -> LossLimitDecision:
        order.append("loss_limit")
        return LossLimitDecision(
            blocked=False, reason=None, cumulative_loss_usd=None, limit_usd=None
        )

    monkeypatch.setattr("services.strategy_engine.app.consumer.check_cooldown", _cd)
    monkeypatch.setattr("services.strategy_engine.app.consumer.check_concurrent_caps", _caps)
    monkeypatch.setattr("services.strategy_engine.app.consumer.check_daily_loss_limit", _ll)
    handler, _ = _build_handler(
        evaluate_result=_scoring_result(decision="execute"),
        monkeypatch=monkeypatch,
    )
    await handler(_envelope(_signal()))
    assert order == ["cooldown", "caps", "loss_limit"]
