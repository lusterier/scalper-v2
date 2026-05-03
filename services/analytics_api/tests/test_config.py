"""Tests for :class:`services.analytics_api.app.config.Settings`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.analytics_api.app.config import Settings


def test_accepts_minimal_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """With DATABASE_URL set, documented defaults apply."""
    for key in ("SERVICE_NAME", "LOG_LEVEL", "HTTP_PORT", "NATS_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "analytics-api"
    assert s.log_level == "INFO"
    assert s.http_port == 8000
    assert s.nats_url == "nats://nats:4222"
    assert s.database_url == "postgresql://u@h/d"


def test_rejects_missing_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """WG#4 — DATABASE_URL is required; missing → ValidationError, not generic Exception."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_extra_env_vars_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """WG#3 — sibling-service env vars must not error; ``extra='ignore'``.

    Compose env_file ``/etc/scalper-v2/secrets.env`` is shared cross-
    service; analytics-api singleton must tolerate the entire other-
    service surface (BOT_ID for strategy-engine per-bot, MARKET_DATA_*
    for market-data-svc, SIGNAL_GATEWAY_HMAC_SECRET for signal-gateway,
    BOT_<ID>_BYBIT_API_KEY for execution-service per-bot creds).
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("BOT_ID", "alpha")
    monkeypatch.setenv("MARKET_DATA_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "irrelevant-here-32chars-padded-x")
    monkeypatch.setenv("FEATURE_ENGINE_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("POSTGRES_USER", "scalper")
    monkeypatch.setenv("BOT_ALPHA_BYBIT_API_KEY", "irrelevant-key")
    Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("env_key", "env_value", "attr", "expected"),
    [
        ("SERVICE_NAME", "analytics-api-staging", "service_name", "analytics-api-staging"),
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
