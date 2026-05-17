"""T-543 (D9) regression pin: native analytics-api SERVICE_NAME identity.

`scripts/dev-up.sh` sources `.env` with `set -a` (auto-export), so the
shared `.env`'s `SERVICE_NAME=signal-gateway` (signal-gateway's own value)
leaks into the environment. The native analytics-api uvicorn launch must
therefore pin `SERVICE_NAME=analytics-api` in its env prefix — exactly as
it already pins `DATABASE_URL`/`NATS_URL`, and exactly as `compose.yaml`
sets it per-service for the containerized path. Without the pin,
analytics-api `Settings()` inherits `signal-gateway` and emits structured
logs with `service=signal-gateway` (a §N2 misattribution — D9).

These tests parse `dev-up.sh` text (no subprocess / no stack) and are
line-continuation robust: the env prefix and the `…:create_app` invocation
sit on different physical lines joined by a trailing backslash.
"""

from __future__ import annotations

from pathlib import Path

_DEV_UP = Path(__file__).resolve().parents[2] / "scripts" / "dev-up.sh"
_CREATE_APP = "services.analytics_api.app.main:create_app"


def _analytics_launch_command() -> str:
    """Return the logical (continuation-joined) shell command that launches
    the native analytics-api uvicorn process."""
    text = _DEV_UP.read_text(encoding="utf-8")
    # Join backslash-newline continuations so the env prefix + the uvicorn
    # invocation become one logical line regardless of physical wrapping.
    logical = text.replace("\\\n", " ")
    matches = [ln for ln in logical.splitlines() if _CREATE_APP in ln]
    assert len(matches) == 1, (
        f"expected exactly one analytics-api uvicorn launch in dev-up.sh, found {len(matches)}"
    )
    return matches[0]


def test_dev_up_pins_analytics_api_service_name() -> None:
    """D9 regression: the native analytics-api launch pins the correct
    SERVICE_NAME, so its structured logs carry service=analytics-api."""
    assert "SERVICE_NAME=analytics-api" in _analytics_launch_command()


def test_dev_up_analytics_launch_does_not_inherit_signal_gateway() -> None:
    """The leaked .env value (SERVICE_NAME=signal-gateway) must not be what
    the analytics-api launch pins — catches a future wrong-value copy-paste."""
    assert "SERVICE_NAME=signal-gateway" not in _analytics_launch_command()
