"""Shared fixtures for alerting-svc unit tests (T-409).

Lets tests exercise the real :func:`create_app` factory and its lifespan
without touching real NATS / Telegram / file-system. Mirror of
analytics-api conftest pattern.

Fixtures:
* ``mock_bus`` — stand-in for :class:`packages.bus.NatsClient`.
* ``mock_telegram_client`` — stand-in for :class:`TelegramClient`.
* ``alerts_yaml_path`` — tmp_path fixture writing a minimal valid YAML +
  resolving env vars for the 4 channels (per WG#8 — tmp_path scope, no
  committed fixture file).
* ``app_with_mocks`` — builds the real app with NatsClient + TelegramClient
  + load_alerts_config patched.
* ``client`` — TestClient that runs the lifespan on entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from packages.bus import ConnectionState as BusConnectionState
from services.alerting.app.config import Settings
from services.alerting.app.main import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI


_VALID_ALERTS_YAML = """\
channels:
  system:
    telegram_chat_id_env: TELEGRAM_CHAT_SYSTEM
  trading:
    telegram_chat_id_env: TELEGRAM_CHAT_TRADING
  pnl:
    telegram_chat_id_env: TELEGRAM_CHAT_PNL
  security:
    telegram_chat_id_env: TELEGRAM_CHAT_SECURITY

rate_limit:
  dedup_window_seconds: 300

rules:
  - event: "*"
    channel: system
    severity: info
    template: templates/default.j2
"""


_DEFAULT_TEMPLATE_J2 = """\
<b>[{{ severity | upper }}]</b> {{ event }}
{% if payload %}<pre>{{ payload | tojson }}</pre>{% endif %}
"""


@pytest.fixture
def alerts_yaml_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write minimal valid alerts.yaml + default.j2 template + set env vars."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("TELEGRAM_CHAT_SYSTEM", "111")
    monkeypatch.setenv("TELEGRAM_CHAT_TRADING", "222")
    monkeypatch.setenv("TELEGRAM_CHAT_PNL", "333")
    monkeypatch.setenv("TELEGRAM_CHAT_SECURITY", "444")

    yaml_path = tmp_path / "alerts.yaml"
    yaml_path.write_text(_VALID_ALERTS_YAML, encoding="utf-8")

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "default.j2").write_text(_DEFAULT_TEMPLATE_J2, encoding="utf-8")
    return yaml_path


@pytest.fixture
def settings(alerts_yaml_path: Path) -> Settings:
    """Settings populated with values safe for in-process tests."""
    return Settings(alerts_yaml_path=str(alerts_yaml_path))  # type: ignore[call-arg]


@pytest.fixture
def mock_bus() -> MagicMock:
    """NatsClient stand-in. Defaults to CONNECTED."""
    bus = MagicMock()
    bus.state = BusConnectionState.CONNECTED
    bus.connect = AsyncMock()
    bus.close = AsyncMock()
    bus.subscribe = AsyncMock()
    return bus


@pytest.fixture
def mock_telegram_client() -> MagicMock:
    """TelegramClient stand-in (sync constructor + async send/aclose)."""
    client = MagicMock()
    client.send = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def app_with_mocks(
    settings: Settings,
    mock_bus: MagicMock,
    mock_telegram_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """Build the real app with NatsClient + TelegramClient patched."""
    monkeypatch.setattr(
        "services.alerting.app.main.NatsClient",
        MagicMock(return_value=mock_bus),
    )
    monkeypatch.setattr(
        "services.alerting.app.main.TelegramClient",
        MagicMock(return_value=mock_telegram_client),
    )
    return create_app(settings=settings)


@pytest.fixture
def client(app_with_mocks: FastAPI) -> Iterator[TestClient]:
    """TestClient that runs the lifespan on entry, teardown on exit."""
    with TestClient(app_with_mocks) as c:
        yield c
