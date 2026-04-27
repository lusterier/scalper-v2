"""Tests for :class:`services.execution.app.config.Settings`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.execution.app.config import Settings


def test_accepts_minimal_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """With only DATABASE_URL set, documented defaults apply."""
    for key in ("SERVICE_NAME", "LOG_LEVEL", "HTTP_PORT", "NATS_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "execution-service"
    assert s.log_level == "INFO"
    assert s.http_port == 8000
    assert s.nats_url == "nats://nats:4222"
    assert s.database_url == "postgresql://u@h/d"


def test_rejects_missing_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_extra_env_vars_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sibling-service env vars must not error.

    Includes future T-215 per-bot Bybit creds (``BOT_<ID>_*``) which
    will live in ``secrets.env`` alongside execution-service env. T-214
    skeleton ignores them; T-215 plan-doc adds the read path.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("POSTGRES_USER", "scalper")
    monkeypatch.setenv("MARKET_DATA_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("FEATURE_ENGINE_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "irrelevant-here-32chars-padded-x")
    monkeypatch.setenv("BOT_ALPHA_BYBIT_API_KEY", "future-t215-key")
    monkeypatch.setenv("BOT_ALPHA_BYBIT_API_SECRET", "future-t215-secret")
    Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("env_key", "env_value", "attr", "expected"),
    [
        ("SERVICE_NAME", "execution-service-staging", "service_name", "execution-service-staging"),
        ("LOG_LEVEL", "DEBUG", "log_level", "DEBUG"),
        ("LOG_LEVEL", "CRITICAL", "log_level", "CRITICAL"),
        ("NATS_URL", "nats://other:4222", "nats_url", "nats://other:4222"),
    ],
)
def test_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    env_value: str,
    attr: str,
    expected: str,
) -> None:
    """Each Settings field is overridable via its uppercase env var."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv(env_key, env_value)
    s = Settings()  # type: ignore[call-arg]
    assert getattr(s, attr) == expected
