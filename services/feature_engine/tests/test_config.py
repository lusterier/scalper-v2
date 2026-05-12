"""Tests for :class:`services.feature_engine.app.config.Settings`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.feature_engine.app.config import Settings


def test_accepts_minimal_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """With only required env vars set, documented defaults apply."""
    for key in ("SERVICE_NAME", "LOG_LEVEL", "HTTP_PORT", "NATS_URL", "FEATURE_ENGINE_SYMBOLS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "feature-engine"
    assert s.log_level == "INFO"
    assert s.http_port == 8000
    assert s.nats_url == "nats://nats:4222"
    assert s.database_url == "postgresql://u@h/d"
    assert s.feature_engine_symbols == ""
    assert s.symbols == []


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
    """Sibling-service env vars must not error."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("POSTGRES_USER", "scalper")
    monkeypatch.setenv("MARKET_DATA_SYMBOLS", "BTCUSDT,ETHUSDT")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "irrelevant-here-32chars-padded-x")
    Settings()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("env_key", "env_value", "attr", "expected"),
    [
        ("SERVICE_NAME", "feature-engine-staging", "service_name", "feature-engine-staging"),
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


# ---------------------------------------------------------------------------
# Settings.symbols (FEATURE_ENGINE_SYMBOLS env-stopgap; mirror MARKET_DATA_SYMBOLS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("", []),
        ("BTCUSDT", ["BTCUSDT"]),
        ("BTCUSDT,ETHUSDT", ["BTCUSDT", "ETHUSDT"]),
        ("BTCUSDT,,ETHUSDT", ["BTCUSDT", "ETHUSDT"]),  # repeated separator → drop empty
        (",BTCUSDT,", ["BTCUSDT"]),  # leading/trailing → drop empty
        ("  BTCUSDT  , ETHUSDT ", ["BTCUSDT", "ETHUSDT"]),  # whitespace stripped
        (",,,", []),  # only separators → empty list
        ("   ", []),  # only whitespace → empty list
    ],
)
def test_feature_engine_symbols_split_strip_drops_empty_fragments(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected: list[str],
) -> None:
    """Empty default + repeated-separator + whitespace cases all parse correctly.

    Mirrors market_data ``test_symbols_split_strip_drops_empty_fragments``.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("FEATURE_ENGINE_SYMBOLS", env_value)
    s = Settings()  # type: ignore[call-arg]
    assert s.symbols == expected


# ---------------------------------------------------------------------------
# T-518 — backfill_window_days + backfill_max_batch_size Settings
# ---------------------------------------------------------------------------


def test_backfill_window_days_default_30(monkeypatch: pytest.MonkeyPatch) -> None:
    """OQ-3=A 2026-05-12 — default 30d back per BRIEF §9.3 + §N9 L-001."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.delenv("BACKFILL_WINDOW_DAYS", raising=False)
    s = Settings()  # type: ignore[call-arg]
    assert s.backfill_window_days == 30


def test_backfill_max_batch_size_default_5000_reserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """RESERVED setting per WG#2 — default 5000 exposed but not enforced in T-518."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.delenv("BACKFILL_MAX_BATCH_SIZE", raising=False)
    s = Settings()  # type: ignore[call-arg]
    assert s.backfill_max_batch_size == 5000


def test_backfill_window_days_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-001 active control — env-overridable window."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("BACKFILL_WINDOW_DAYS", "7")
    s = Settings()  # type: ignore[call-arg]
    assert s.backfill_window_days == 7
