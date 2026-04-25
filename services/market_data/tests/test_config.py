"""Tests for :class:`services.market_data.app.config.Settings`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.market_data.app.config import Settings


def test_accepts_minimal_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """With only required env vars set, documented defaults apply."""
    for key in ("SERVICE_NAME", "LOG_LEVEL", "HTTP_PORT", "NATS_URL", "MARKET_DATA_SYMBOLS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    s = Settings()  # type: ignore[call-arg]
    assert s.service_name == "market-data-svc"
    assert s.log_level == "INFO"
    assert s.http_port == 8000
    assert s.nats_url == "nats://nats:4222"
    assert s.database_url == "postgresql://u@h/d"
    assert s.market_data_symbols == ""
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
    """POSTGRES_USER + SIGNAL_GATEWAY_HMAC_SECRET are sibling-service vars; must not error."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("POSTGRES_USER", "scalper")
    monkeypatch.setenv("SIGNAL_GATEWAY_HMAC_SECRET", "irrelevant-here-32chars-padded-x")
    Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Settings.symbols — flag #4 (empty-string-safe split)
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
def test_symbols_split_strip_drops_empty_fragments(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected: list[str],
) -> None:
    """Empty default + repeated-separator + whitespace cases all parse correctly."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u@h/d")
    monkeypatch.setenv("MARKET_DATA_SYMBOLS", env_value)
    s = Settings()  # type: ignore[call-arg]
    assert s.symbols == expected
