"""T-544 regression pin: the `strategy-engine-smoke` compose service.

`compose.yaml` ships per-bot `strategy-engine-<bot>` services (alpha, beta);
the F5_E2 deployment smoke (`docs/runbooks/F5_E2_shadow_smoke.md`) needs a
`strategy-engine-smoke` for the `smoke` fixture bot. It must be a verbatim
instance of the parameterized pattern — identical to `strategy-engine-beta`
except `BOT_ID` / `BOT_CONFIRM_LIVE`. The dev overlay must likewise mirror
the alpha/beta overlay verbatim (no per-bot keys).

`compose.yaml` has no custom tags → plain ``yaml.safe_load``. ``compose.dev
.yaml`` uses Compose-merge tags (``!reset`` / ``!override``) which
``safe_load`` cannot construct (raises ``ConstructorError``); a tolerant
loader maps every unknown ``!``-tag to ``None`` (the precise value is
irrelevant — beta and smoke resolve identically, so verbatim equality holds).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "compose.yaml"
_COMPOSE_DEV = _REPO_ROOT / "compose.dev.yaml"
_NORMALISE_KEYS = ("BOT_ID", "BOT_CONFIRM_LIVE")
_SENTINEL = "<normalised>"


class _TolerantLoader(yaml.SafeLoader):
    """SafeLoader that ignores Compose-merge custom tags (!reset/!override)."""


def _ignore_unknown(loader: Any, tag_suffix: str, node: Any) -> None:
    return None


_TolerantLoader.add_multi_constructor(  # type: ignore[no-untyped-call]  # pyyaml BaseConstructor.add_multi_constructor is unstubbed
    "!", _ignore_unknown
)


def _services(path: Path, *, tolerant: bool) -> dict[str, Any]:
    loader = _TolerantLoader if tolerant else yaml.SafeLoader
    doc = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
    services: dict[str, Any] = doc["services"]
    return services


def _normalised(service: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy with the two per-bot env keys overwritten to a sentinel."""
    out = copy.deepcopy(service)
    env = out.get("environment", {})
    for key in _NORMALISE_KEYS:
        if key in env:
            env[key] = _SENTINEL
    return out


def test_compose_has_strategy_engine_smoke_service() -> None:
    """strategy-engine-smoke exists with the smoke bot identity + infra deps."""
    smoke = _services(_COMPOSE, tolerant=False)["strategy-engine-smoke"]
    assert smoke["environment"]["BOT_ID"] == "smoke"
    assert smoke["environment"]["SERVICE_NAME"] == "strategy-engine"
    assert smoke["image"] == "scalper-v2/strategy-engine:dev"
    assert {"postgres", "nats", "nats-init"} <= set(smoke["depends_on"])
    assert "healthcheck" in smoke


def test_smoke_mirrors_beta_exactly_modulo_bot_keys() -> None:
    """Load-bearing pin: strategy-engine-smoke == strategy-engine-beta once
    BOT_ID + BOT_CONFIRM_LIVE are normalised on BOTH — any drift from the
    parameterized pattern (image, deps, healthcheck, env, …) red-fails."""
    services = _services(_COMPOSE, tolerant=False)
    assert _normalised(services["strategy-engine-smoke"]) == _normalised(
        services["strategy-engine-beta"]
    )


def test_dev_overlay_smoke_mirrors_beta_verbatim() -> None:
    """compose.dev.yaml strategy-engine-smoke overlay equals the beta overlay
    verbatim — the dev overlay carries no per-bot keys (no substitution)."""
    services = _services(_COMPOSE_DEV, tolerant=True)
    assert services["strategy-engine-smoke"] == services["strategy-engine-beta"]
