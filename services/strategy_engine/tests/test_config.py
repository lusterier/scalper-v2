"""Tests for :class:`services.strategy_engine.app.config.Settings`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.strategy_engine.app.config import Settings


def test_accepts_minimal_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """With DATABASE_URL + BOT_ID set, documented defaults apply."""
    for key in ("SERVICE_NAME", "LOG_LEVEL", "HTTP_PORT", "NATS_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("BOT_ID", "alpha")
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "strategy-engine"
    assert s.log_level == "INFO"
    assert s.http_port == 8000
    assert s.nats_url == "nats://nats:4222"
    assert s.database_url == "postgresql://u@h/d"
    assert s.bot_id == "alpha"
    assert s.bot_config_dir == "/app/configs/bots"
    assert s.plugin_registry_path == "/app/configs/plugin_registry.yaml"


def test_rejects_missing_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BOT_ID", "alpha")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_rejects_missing_bot_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """BOT_ID is required per BRIEF §9.4:1530 — no default, fail-fast."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.delenv("BOT_ID", raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_rejects_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("BOT_ID", "alpha")
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_extra_env_vars_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sibling-service env vars must not error — extra='ignore'."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("BOT_ID", "alpha")
    monkeypatch.setenv("POSTGRES_USER", "scalper")
    monkeypatch.setenv("MARKET_DATA_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("FEATURE_ENGINE_SYMBOLS", "BTCUSDT")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "irrelevant-here-32chars-padded-x")
    monkeypatch.setenv("BOT_ALPHA_BYBIT_API_KEY", "irrelevant-key")
    Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("env_key", "env_value", "attr", "expected"),
    [
        ("SERVICE_NAME", "strategy-engine-staging", "service_name", "strategy-engine-staging"),
        ("LOG_LEVEL", "DEBUG", "log_level", "DEBUG"),
        ("LOG_LEVEL", "CRITICAL", "log_level", "CRITICAL"),
        ("NATS_URL", "nats://other:4222", "nats_url", "nats://other:4222"),
        ("BOT_CONFIG_DIR", "/etc/scalper/bots", "bot_config_dir", "/etc/scalper/bots"),
        (
            "PLUGIN_REGISTRY_PATH",
            "/etc/scalper/plugins.yaml",
            "plugin_registry_path",
            "/etc/scalper/plugins.yaml",
        ),
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
    monkeypatch.setenv("BOT_ID", "alpha")
    monkeypatch.setenv(env_key, env_value)
    s = Settings()  # type: ignore[call-arg]
    assert getattr(s, attr) == expected


def test_signal_max_age_seconds_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-310b: Settings.signal_max_age_seconds defaults to 600 + env-overridable."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("BOT_ID", "alpha")
    monkeypatch.delenv("SIGNAL_MAX_AGE_SECONDS", raising=False)
    s = Settings()  # type: ignore[call-arg]
    assert s.signal_max_age_seconds == 600
    monkeypatch.setenv("SIGNAL_MAX_AGE_SECONDS", "300")
    s2 = Settings()  # type: ignore[call-arg]
    assert s2.signal_max_age_seconds == 300
