"""Tests for :class:`services.signal_gateway.app.config.Settings`."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from services.signal_gateway.app.config import Settings


def test_accepts_minimal_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """With only required env vars set, documented defaults apply."""
    for key in ("SERVICE_NAME", "LOG_LEVEL", "HTTP_PORT", "NATS_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "shh")
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "signal-gateway"
    assert s.log_level == "INFO"
    assert s.http_port == 8000
    assert s.nats_url == "nats://nats:4222"
    assert s.database_url == "postgresql://u@h/d"
    assert isinstance(s.signal_gateway_hmac_secret, SecretStr)


def test_rejects_missing_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "shh")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_rejects_missing_hmac_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.delenv("SIGNAL_GATEWAY_HMAC_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "shh")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_secret_is_redacted_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "the-actual-secret-42")
    s = Settings()  # type: ignore[call-arg]
    assert "the-actual-secret-42" not in repr(s)


def test_extra_env_vars_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSTGRES_USER + TUNNEL_TOKEN are sibling-service vars; must not error."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "shh")
    monkeypatch.setenv("POSTGRES_USER", "scalper")
    monkeypatch.setenv("TUNNEL_TOKEN", "xyz")
    Settings()  # type: ignore[call-arg]
