"""F3 E2 exit-criterion test (T-313).

§19:2548 verbatim: "scoring_evaluations shows per-rule audit for every signal."

Verifies T-310b consumer body's call to `insert_scoring_evaluation` carries:
- `rule_results` list with one entry per scoring rule (per-rule audit shape)
- `feature_snapshot` non-empty dict (resolver populated at least one entry)

Net-new vs T-310b consumer tests: T-310b pins ORDERING (db_insert before
bus_publish) only; T-313 E2 pins per-rule audit CONTENT shape per WG#2 reuse
of T-310b _build_handler factory pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from packages.bus import MessageEnvelope
from packages.bus.schemas.signals import SignalValidated
from packages.features.types import FeatureValue
from packages.scoring import load_bot_config
from packages.scoring.registry import load_plugin_registry
from packages.scoring.resolver import ResolverResult
from services.strategy_engine.app.consumer import make_signal_handler

if TYPE_CHECKING:
    import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _signal() -> SignalValidated:
    return SignalValidated(
        source="webhook",
        idempotency_key="key-1",
        received_at=_FIXED_NOW,
        symbol="BTCUSDT",
        original_symbol="BTCUSDT",
        action="LONG",
        expires_at=_FIXED_NOW + timedelta(seconds=60),
        payload={},
    )


def _envelope(signal: SignalValidated) -> MessageEnvelope:
    return MessageEnvelope(
        correlation_id="corr-1",  # type: ignore[arg-type]
        publisher="signal-gateway",
        payload=signal.model_dump(mode="json"),
    )


def _ok_resolver() -> MagicMock:
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=ResolverResult(
            value=FeatureValue(value_num=Decimal("100")),
            status="ok",
        ),
    )
    # T-520 sub-commit #2 — beta.yaml uses oi_squeeze plugin which triggers
    # resolver.resolve_history via _required_history_window. Mock as no-op.
    resolver.resolve_history = AsyncMock(return_value=[])
    return resolver


def _mock_pool_with_signal_id(signal_id: int = 42) -> MagicMock:
    """Mock pool returning signal_id for select + recording execute call."""
    pool = MagicMock()
    fake_conn = MagicMock()
    fake_conn.fetchrow = AsyncMock(return_value={"id": signal_id})
    fake_conn.execute = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _mock_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


async def test_scoring_evaluations_records_one_rule_result_entry_per_scoring_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2 #1 — rule_results length matches len(bot.scoring.rules) for both bots."""
    plugin_registry = load_plugin_registry(_REPO_ROOT / "configs" / "plugin_registry.yaml")

    captured_kwargs: list[dict[str, Any]] = []

    async def _capture_insert(_conn: Any, **kwargs: Any) -> None:
        captured_kwargs.append(kwargs)

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.insert_scoring_evaluation",
        _capture_insert,
    )

    for bot_name in ("alpha", "beta"):
        bot_config = load_bot_config(
            _REPO_ROOT / "configs" / "bots" / f"{bot_name}.yaml",
            plugin_registry=plugin_registry,
        )
        handler = make_signal_handler(
            bot_id=bot_config.bot_id,  # type: ignore[arg-type]
            bot_config=bot_config,
            resolver=_ok_resolver(),
            pool=_mock_pool_with_signal_id(),
            bus=_mock_bus(),
            trading_logger=MagicMock(),
            system_logger=MagicMock(),
            audit_logger=MagicMock(),
            now_fn=lambda: _FIXED_NOW,
            max_signal_age_seconds=600,
        )
        await handler(_envelope(_signal()))

    # Two bots, two INSERT calls — each rule_results length matches its scoring.rules count.
    assert len(captured_kwargs) == 2
    for kwargs in captured_kwargs:
        rule_results = kwargs["rule_results"]
        assert isinstance(rule_results, list)
        assert len(rule_results) == 1  # alpha + beta both have exactly 1 rule per fixtures


async def test_scoring_evaluations_feature_snapshot_non_empty_after_resolver_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2 #2 — feature_snapshot non-empty dict after T-307 evaluator populates."""
    plugin_registry = load_plugin_registry(_REPO_ROOT / "configs" / "plugin_registry.yaml")
    bot_config = load_bot_config(
        _REPO_ROOT / "configs" / "bots" / "alpha.yaml",
        plugin_registry=plugin_registry,
    )

    captured_kwargs: list[dict[str, Any]] = []

    async def _capture_insert(_conn: Any, **kwargs: Any) -> None:
        captured_kwargs.append(kwargs)

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.insert_scoring_evaluation",
        _capture_insert,
    )

    handler = make_signal_handler(
        bot_id=bot_config.bot_id,  # type: ignore[arg-type]
        bot_config=bot_config,
        resolver=_ok_resolver(),
        pool=_mock_pool_with_signal_id(),
        bus=_mock_bus(),
        trading_logger=MagicMock(),
        system_logger=MagicMock(),
        audit_logger=MagicMock(),
        now_fn=lambda: _FIXED_NOW,
        max_signal_age_seconds=600,
    )
    await handler(_envelope(_signal()))

    assert len(captured_kwargs) == 1
    feature_snapshot = captured_kwargs[0]["feature_snapshot"]
    assert isinstance(feature_snapshot, dict)
    assert len(feature_snapshot) > 0
