"""F3 E3 exit-criterion test (T-313).

§19:2549 verbatim: "Passthrough mode verified: every signal executes while
scoring_evaluations shows decision."

Net-new vs T-307 unit pins: end-to-end YAML → load_bot_config → T-310b
consumer flow → audit row decision string pin (T-307 unit-pinned passthrough
on synthetic ScoringConfig sub-tree; T-313 E3 pins full BotConfig E2E).
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


async def test_passthrough_mode_records_decision_passthrough_in_audit_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E3 — beta.yaml (mode=passthrough) → scoring_evaluations.decision == 'passthrough' E2E."""
    plugin_registry = load_plugin_registry(_REPO_ROOT / "configs" / "plugin_registry.yaml")
    beta = load_bot_config(
        _REPO_ROOT / "configs" / "bots" / "beta.yaml",
        plugin_registry=plugin_registry,
    )

    captured_kwargs: list[dict[str, Any]] = []

    async def _capture_insert(_conn: Any, **kwargs: Any) -> None:
        captured_kwargs.append(kwargs)

    monkeypatch.setattr(
        "services.strategy_engine.app.consumer.insert_scoring_evaluation",
        _capture_insert,
    )

    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=ResolverResult(
            value=FeatureValue(value_num=Decimal("100")),
            status="ok",
        ),
    )

    pool = MagicMock()
    fake_conn = MagicMock()
    fake_conn.fetchrow = AsyncMock(return_value={"id": 42})
    fake_conn.execute = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)

    bus = MagicMock()
    bus.publish = AsyncMock()

    signal = SignalValidated(
        source="webhook",
        idempotency_key="key-1",
        received_at=_FIXED_NOW,
        symbol="BTCUSDT",
        original_symbol="BTCUSDT",
        action="LONG",
        expires_at=_FIXED_NOW + timedelta(seconds=60),
        payload={},
    )
    envelope = MessageEnvelope(
        correlation_id="corr-1",  # type: ignore[arg-type]
        publisher="signal-gateway",
        payload=signal.model_dump(mode="json"),
    )

    handler = make_signal_handler(
        bot_id=beta.bot_id,  # type: ignore[arg-type]
        bot_config=beta,
        resolver=resolver,
        pool=pool,
        bus=bus,
        trading_logger=MagicMock(),
        system_logger=MagicMock(),
        audit_logger=MagicMock(),
        now_fn=lambda: _FIXED_NOW,
        max_signal_age_seconds=600,
    )
    await handler(envelope)

    assert len(captured_kwargs) == 1
    kwargs = captured_kwargs[0]
    assert kwargs["decision"] == "passthrough"
    # Full per-rule audit recorded even in passthrough mode.
    assert isinstance(kwargs["rule_results"], list)
    assert len(kwargs["rule_results"]) == 1
