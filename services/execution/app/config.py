"""Pydantic Settings model for execution-service (§5.11).

All env-backed configuration lives in one typed model. Validation
happens at startup: a missing or malformed value fails the lifespan
before uvicorn binds the port, not at first request. Mirrors the
feature-engine pattern (T-109), itself a mirror of market-data-svc
(T-100) and signal-gateway (T-015a).

T-214 shipped the minimum 5 fields (service identity + DSNs); T-215
adds 8 fields for SharedRateLimiter (per ADR-0003) + BybitV5Adapter
leverage cache TTL (per L-001 fix).

Per-bot env vars consumed by :mod:`services.execution.app.pool` at
lifespan startup (NOT through this :class:`Settings` model — these are
read directly from ``os.environ`` by the composition root since
pydantic-settings doesn't natively model dynamic ``BOT_<ID>_*`` keys):

* ``BOT_<ID>_BYBIT_API_KEY`` — H-022 per-bot key.
* ``BOT_<ID>_BYBIT_API_SECRET`` — H-022 per-bot secret.
* ``BOT_<ID>_BYBIT_SUB_ACCOUNT`` — sub-account string per ADR-0004
  (supersedes ADR-0003 §Decision 6 sub_account-from-column path).
* ``BOT_<ID>_PAPER_SEED_BALANCE`` — paper bot starting balance (Decimal-as-str).
* ``BOT_<ID>_PAPER_SLIPPAGE_MODEL`` — one of ``fixed_pct`` /
  ``proportional_to_qty`` / ``half_spread``.
* ``BOT_<ID>_PAPER_FEE_RATE`` — paper bot fee rate (Decimal-as-str).
* ``BOT_<ID>_PAPER_SLIPPAGE_PARAMS_JSON`` — JSON-encoded
  ``dict[str, Decimal-as-str]`` for the chosen slippage_model's required keys.

F2-specific fields landing in later owner tasks per §0.8 anti-hypothetical:

* ``POSITION_POLL_INTERVAL`` — T-217 PositionLifecycle FSM tick rate.
* ``RECONCILE_SLEEP_SECONDS`` — T-219 cumulative-delta closed-pnl
  snapshot delay (H-011 default 2s, configurable per L-001).
* ``AUDIT_LOOP_INTERVAL_SECONDS`` — T-220 APScheduler period.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["LogLevel", "Settings"]


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Env-sourced configuration for the execution-service.

    Field names are lowercase-with-underscores; env vars are the
    uppercase equivalent (pydantic-settings handles the mapping).
    ``extra="ignore"`` tolerates sibling env vars (``POSTGRES_USER``,
    ``MARKET_DATA_SYMBOLS``, ``SIGNAL_GATEWAY_HMAC_SECRET``,
    ``FEATURE_ENGINE_SYMBOLS``, ``BOT_<ID>_BYBIT_API_KEY/SECRET``, …)
    that live in ``.env.example`` or ``secrets.env`` but do not belong
    to this T-214 skeleton (the per-bot Bybit creds belong to T-215).
    """

    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "execution-service"
    log_level: LogLevel = "INFO"
    http_port: int = 8000

    # Message bus — NATS JetStream (§8, §16.6).
    nats_url: str = "nats://nats:4222"

    # asyncpg DSN; scheme-validated by packages.db.create_pool at pool init.
    database_url: str

    # T-215 — BybitV5Adapter LRU leverage cache TTL (L-001 fix; Q9 brief default 1h).
    bybit_leverage_cache_ttl_s: float = 3600.0

    # T-215 — SharedRateLimiter ctor params per ADR-0003 §Decision 1 (defaults verbatim).
    rate_limit_orders_rate: float = 10.0
    rate_limit_orders_capacity: float = 20.0
    rate_limit_positions_rate: float = 10.0
    rate_limit_positions_capacity: float = 20.0
    rate_limit_ip_global_rate: float = 120.0
    rate_limit_ip_global_capacity: float = 240.0
    rate_limit_pause_ms: int = 500

    # T-216a — placement-handler fill_price retry knobs (CONCERN #7 fix per L-001).
    execution_fill_price_retry_attempts: int = 3
    execution_fill_price_retry_backoff_s: float = 0.1

    # T-216b2 — OrderRequestDedupConsumer per-bot ring capacity (H-009; OQ-7/OQ-8).
    execution_orders_dedup_capacity: int = 10000

    # T-218a — ExecutionDispatcher per-bot ring capacity (H-009; §9.5:1591 size-10k default).
    dispatch_dedup_capacity: int = 10000

    # T-217a — PositionLifecycle FSM monitor (§9.5:1585-1592 / §N9 configurability).
    position_poll_interval_s: float = 1.0
    position_poll_stale_ticks: int = 5

    # T-219 — closed-pnl post-close sleep (H-011; ADR-0006 D2).
    execution_closed_pnl_post_close_sleep_s: float = 2.0

    # T-220 — P&L audit loop (§9.5:1601-1605; H-017; ADR-0007 D7).
    execution_audit_tick_interval_seconds: int = 300
    execution_audit_window_seconds: int = 10800
    execution_audit_divergence_threshold_usd: Decimal = Decimal("0.50")

    # T-531 — equity snapshot tick (§15.3 virtual_balance; §N9/L-001; ADR-0007 D7).
    execution_equity_snapshot_interval_seconds: int = 300

    # T-534b2 — SL watchdog tick (H-028; §N9/L-001; ADR-0007 D7; ADR-0011 SL/TP cluster).
    execution_sl_watchdog_tick_interval_seconds: int = 300
    execution_sl_watchdog_missing_threshold_ticks: int = 3

    # T-536 — trailing SL audit tick (drift detection; §N9/L-001; ADR-0007 D7).
    execution_trail_audit_tick_interval_seconds: int = 300
    execution_trail_audit_drift_tolerance_pct: Decimal = Decimal("0.001")

    # T-532b — funding-fee poll tick (ADR-0011 funding-fee tracking; windowed
    # pull mirror execution_audit_* interval+window; §N9/L-001; ADR-0007 D7).
    execution_funding_fee_poll_interval_seconds: int = 300
    execution_funding_fee_poll_window_seconds: int = 10800

    # T-221 — post-restart reconciliation race-window (H-026 spec literal 60s).
    execution_reconcile_race_window_seconds: int = 60

    # T-511b2 / ADR-0010 — ShadowWorker per-variant PaperExchange seed.
    # NOT per-bot per BotConfig.shadow.fee_rate (deliberate — shadow simulation
    # isolated from paper-bot fee config; allows shadow-only fee scenario
    # testing). Future task can promote to per-bot YAML if a use case surfaces.
    shadow_seed_balance_usd: Decimal = Decimal("10000")
    shadow_fee_rate: Decimal = Decimal("0.0006")

    # T-512a / BRIEF §13.4 / H-023 — shadow variant restart-recovery via OHLC
    # replay. Bounded for safety: extreme stuck variants (created_at outliers)
    # don't DoS startup; per-variant compute timeout caps runaway loop. 48h
    # window covers 2x ShadowConfig.max_duration_hours upper bound (24h);
    # 120s timeout = ~4000x compute margin (48h x 2880 candles x ~10us each
    # = ~30ms expected) / ~400x I/O margin (asyncpg cursor prefetch=1000 = ~0.3s).
    shadow_replay_query_window_max_hours: Decimal = Decimal("48")
    shadow_replay_per_variant_timeout_seconds: float = 120.0

    # T-513a / BRIEF §13.5 — rejected-signal observation FSM.
    # Always-on per BRIEF spec ("Separate from variants"); operational kill-
    # switch via `shadow_rejected_enabled`. 60-min observation window per
    # BRIEF §13.5 default; configurable per §N9 + L-001 active control.
    shadow_rejected_enabled: bool = True
    shadow_rejected_observation_minutes: int = 60

    # T-513b1 / BRIEF §13.5 + §20 H-023 — rejected-signal observation
    # restart-recovery via OHLC replay. Mirror T-512a `shadow_replay_*`
    # Settings values for symmetry: 48h window cap covers 2x default
    # observation window with margin (defensive against extreme stuck
    # observations); 120s per-task compute timeout adequate for pure-
    # observer FSM (no PE construction; lighter than variant replay).
    shadow_rejected_replay_query_window_max_hours: Decimal = Decimal("48")
    shadow_rejected_replay_per_observation_timeout_seconds: float = 120.0
