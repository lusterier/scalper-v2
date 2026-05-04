"""Tests for config loader + app factory + health endpoints (T-409, 5 tests).

Covers WG#7 mandatory test count: this is the 5-test ``config/lifespan/
health`` slice. Uses tmp_path-only fixtures per WG#8.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services.alerting.app.config import AlertsConfig, load_alerts_config

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient


_VALID_YAML = """\
channels:
  system:
    telegram_chat_id_env: TELEGRAM_CHAT_SYSTEM
rate_limit:
  dedup_window_seconds: 300
rules:
  - event: "*"
    channel: system
    severity: info
    template: templates/default.j2
"""


def test_load_alerts_config_from_yaml_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tmp_path fixture YAML loads + resolves env-var chat_id."""
    monkeypatch.setenv("TELEGRAM_CHAT_SYSTEM", "12345")
    yaml_path = tmp_path / "alerts.yaml"
    yaml_path.write_text(_VALID_YAML, encoding="utf-8")
    cfg = load_alerts_config(yaml_path)
    assert isinstance(cfg, AlertsConfig)
    assert "system" in cfg.channels
    assert cfg.channel_chat_ids["system"] == "12345"
    assert cfg.rate_limit.dedup_window_seconds == 300
    assert len(cfg.rules) == 1
    assert cfg.rules[0].event == "*"


def test_load_alerts_config_raises_on_missing_telegram_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Channel references env var that's unset → ValueError on load."""
    monkeypatch.delenv("TELEGRAM_CHAT_SYSTEM", raising=False)
    yaml_path = tmp_path / "alerts.yaml"
    yaml_path.write_text(_VALID_YAML, encoding="utf-8")
    with pytest.raises(ValueError, match="TELEGRAM_CHAT_SYSTEM"):
        load_alerts_config(yaml_path)


def test_app_factory_creates_FastAPI_with_health_route(client: TestClient) -> None:
    """create_app + lifespan startup → /health route present + responds 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_ready_endpoint_returns_200_when_bus_connected(client: TestClient) -> None:
    """Bus state == CONNECTED (default fixture) → /ready returns 200."""
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True}


def test_lifespan_subscribes_to_system_alerts(client: TestClient, app_with_mocks: object) -> None:
    """Lifespan startup awaits bus.subscribe('system.alerts', handler)."""
    bus = app_with_mocks.state.bus  # type: ignore[attr-defined]
    bus.subscribe.assert_awaited_once()
    args = bus.subscribe.await_args.args
    assert args[0] == "system.alerts"
