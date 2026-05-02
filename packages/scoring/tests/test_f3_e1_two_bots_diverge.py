"""F3 E1 exit-criterion test (T-313).

§19:2547 verbatim: "Two bots with different scoring configs coexist and react
differently to the same signal."

Loads `configs/bots/alpha.yaml` (active mode) + `configs/bots/beta.yaml`
(passthrough mode), runs `evaluate(...)` with the same signal against both,
asserts decision strings differ ("execute" vs "passthrough").

Mock FeatureResolver returns deterministic OI value per WG#1 alpha.yaml
test mock contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from packages.bus.schemas.signals import SignalValidated
from packages.features.types import FeatureValue
from packages.scoring import evaluate, load_bot_config
from packages.scoring.registry import load_plugin_registry
from packages.scoring.resolver import ResolverResult

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


def _ok_resolver() -> MagicMock:
    """Mock resolver returning OI=100 for any feature_ref. Mirror T-307 _ok_resolver."""
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=ResolverResult(
            value=FeatureValue(value_num=Decimal("100")),
            status="ok",
        ),
    )
    return resolver


async def test_two_bots_react_differently_to_same_signal() -> None:
    """E1 §19:2547 — alpha (active) decides execute; beta (passthrough) decides passthrough."""
    plugin_registry = load_plugin_registry(_REPO_ROOT / "configs" / "plugin_registry.yaml")
    alpha = load_bot_config(
        _REPO_ROOT / "configs" / "bots" / "alpha.yaml",
        plugin_registry=plugin_registry,
    )
    beta = load_bot_config(
        _REPO_ROOT / "configs" / "bots" / "beta.yaml",
        plugin_registry=plugin_registry,
    )
    signal = _signal()
    resolver = _ok_resolver()
    logger = MagicMock()

    alpha_result = await evaluate(
        bot_config=alpha,
        signal=signal,
        resolver=resolver,
        bound_logger=logger,
    )
    beta_result = await evaluate(
        bot_config=beta,
        signal=signal,
        resolver=resolver,
        bound_logger=logger,
    )

    # Same signal in → different decisions out (E1 verbatim "react differently").
    assert alpha_result.decision == "execute"
    assert beta_result.decision == "passthrough"
