"""Pydantic Settings + alerts.yaml loader for alerting-svc (T-409).

Settings cover env-sourced runtime config (Telegram bot token, NATS URL,
log level, retry tuning per BLOCKER #1 + L-001). AlertsConfig parses the
declarative `configs/alerts.yaml` per BRIEF §B.5 (channels + rate_limit
+ rules with optional per-rule threshold).

Channel env-var resolution happens at lifespan startup (per Edge case
#15 — no hot-reload; service restart required to pick up env changes).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "AlertThreshold",
    "AlertsConfig",
    "ChannelConfig",
    "LogLevel",
    "RateLimitConfig",
    "RuleConfig",
    "Settings",
    "load_alerts_config",
]


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-sourced runtime configuration for alerting-svc.

    `extra="ignore"` tolerates sibling env vars (DATABASE_URL,
    BOT_<id>_BYBIT_*, etc.) shared across the compose env_file.
    """

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "alerting"
    log_level: LogLevel = "INFO"
    http_port: int = 8000

    nats_url: str = "nats://nats:4222"

    # Telegram bot credentials (per OQ-8=A — env-resolved, fail-fast on missing)
    telegram_bot_token: str = Field(
        ...,
        min_length=1,
        description="Telegram bot token from @BotFather (required; no default).",
    )

    # alerts.yaml path (per OQ-6=A — repo-root configs/alerts.yaml mounted into container)
    alerts_yaml_path: str = "/app/configs/alerts.yaml"

    # T-409 retry tuning (per BLOCKER #1 fix + L-001 — operational knobs,
    # not mathematically fixed). Defaults chosen for F4 single-operator
    # scale; tunable without redeploy via env vars (ALERTING_MAX_RETRIES
    # + ALERTING_INITIAL_BACKOFF_S) for incident-time adjustments.
    # Mirror T-408 SSE knobs Settings pattern.
    alerting_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Critical alert retry count on Telegram API failure (OQ-3=A).",
    )
    alerting_initial_backoff_s: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Initial exponential backoff seconds; doubles per retry (OQ-3=A).",
    )


class ChannelConfig(BaseModel):
    """Per-channel Telegram chat config (per BRIEF §B.5)."""

    model_config = ConfigDict(frozen=True)

    telegram_chat_id_env: str = Field(
        ...,
        min_length=1,
        description="Env var name holding the Telegram chat_id for this channel.",
    )


class RateLimitConfig(BaseModel):
    """Dedup-window config (per BRIEF §B.5 rate_limit block)."""

    model_config = ConfigDict(frozen=True)

    dedup_window_seconds: int = Field(
        ...,
        ge=1,
        le=86400,
        description="Same alert type deduplicated within this many seconds.",
    )


class AlertThreshold(BaseModel):
    """Optional per-rule numeric threshold gate (per BRIEF §B.5 example)."""

    model_config = ConfigDict(frozen=True)

    field: str = Field(..., min_length=1)
    min: float


class RuleConfig(BaseModel):
    """Single alert routing rule (per BRIEF §B.5)."""

    model_config = ConfigDict(frozen=True)

    event: str = Field(
        ...,
        min_length=1,
        description="Event name to match; '*' is the catch-all wildcard.",
    )
    channel: str = Field(..., min_length=1)
    severity: Literal["info", "warning", "critical"]
    template: str = Field(..., min_length=1)
    threshold: AlertThreshold | None = None


class AlertsConfig(BaseModel):
    """Top-level alerts.yaml shape (per BRIEF §B.5)."""

    model_config = ConfigDict(frozen=True)

    channels: dict[str, ChannelConfig]
    rate_limit: RateLimitConfig
    rules: list[RuleConfig]
    # Resolved env-var values for each channel; populated post-load.
    channel_chat_ids: dict[str, str]

    def find_rule(self, event_name: str) -> RuleConfig | None:
        """Return first rule whose `event` matches `event_name` (exact > wildcard).

        Per OQ-5=A — `event: "*"` literal is the catch-all sentinel; matched
        only when no exact-match rule exists. Operator publishing literal
        `event: "*"` is a degenerate case (would map to itself).
        """
        wildcard: RuleConfig | None = None
        for rule in self.rules:
            if rule.event == event_name:
                return rule
            if rule.event == "*" and wildcard is None:
                wildcard = rule
        return wildcard


def load_alerts_config(path: Path) -> AlertsConfig:
    """Parse + validate `configs/alerts.yaml` (per BRIEF §B.5).

    Validates at load time:

    * YAML parses to a dict.
    * Each `rules[].channel` references a defined `channels[*]` key.
    * Each `channels[*].telegram_chat_id_env` resolves to a non-empty
      env var value (fail-fast per OQ-8=A — service crashes if any
      required env var is missing).
    * `rate_limit.dedup_window_seconds > 0`.

    Raises FileNotFoundError if path missing; ValueError on schema /
    env-resolution failure.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"alerts.yaml top-level must be a mapping; got {type(raw).__name__}"
        raise ValueError(msg)

    channels_raw: Any = raw.get("channels", {})
    rate_limit_raw: Any = raw.get("rate_limit", {})
    rules_raw: Any = raw.get("rules", [])

    if not isinstance(channels_raw, dict) or not channels_raw:
        msg = "alerts.yaml must define non-empty `channels` mapping"
        raise ValueError(msg)
    if not isinstance(rules_raw, list):
        msg = "alerts.yaml `rules` must be a list"
        raise ValueError(msg)

    channels = {name: ChannelConfig(**cfg) for name, cfg in channels_raw.items()}
    rate_limit = RateLimitConfig(**rate_limit_raw)
    rules = [RuleConfig(**r) for r in rules_raw]

    # Validate rule.channel references.
    for rule in rules:
        if rule.channel not in channels:
            msg = (
                f"rule for event {rule.event!r} references undefined "
                f"channel {rule.channel!r}; defined: {sorted(channels)}"
            )
            raise ValueError(msg)

    # Resolve env-var-backed chat_ids (fail-fast per OQ-8=A).
    channel_chat_ids: dict[str, str] = {}
    for channel_name, channel_cfg in channels.items():
        env_name = channel_cfg.telegram_chat_id_env
        env_value = os.environ.get(env_name, "")
        if not env_value:
            msg = (
                f"channel {channel_name!r} requires env var {env_name!r} (must be set + non-empty)"
            )
            raise ValueError(msg)
        channel_chat_ids[channel_name] = env_value

    return AlertsConfig(
        channels=channels,
        rate_limit=rate_limit,
        rules=rules,
        channel_chat_ids=channel_chat_ids,
    )
