"""FastAPI app factory + lifespan for alerting-svc (T-409, §9.7, §N6).

:func:`create_app` is the factory passed to ``uvicorn --factory``. Each
call returns a fresh :class:`fastapi.FastAPI` instance.

T-409 ships the full alerting service: alerts.yaml load → telegram client
init → bus connect → dedup init → state attach → subscribe to
``system.alerts``.

Composition split (mirrors T-309 strategy-engine + T-400 analytics-api):

* **Synchronous primitives** (Settings, structlog logger, Prometheus
  registry, alerts.yaml loader, jinja2 environment) instantiated in
  :func:`create_app` body and attached to ``app.state`` immediately so
  dependency providers in :mod:`.deps` see them before lifespan runs.
* **Asynchronous resources** (:class:`packages.bus.NatsClient`,
  :class:`TelegramClient`, NATS subscription) live in the lifespan and
  attach inside the ``async with`` block.

Lifespan order (load-bearing):

1. ``alerts_config = load_alerts_config(...)`` — fail-fast on missing
   alerts.yaml / unresolved env vars.
2. ``telegram_client = TelegramClient(...)`` — no network call yet.
3. ``bus = NatsClient(...); await bus.connect()``.
4. ``dedup = DedupTracker(...)`` — empty in-memory map.
5. State attach: alerts_config / telegram_client / bus / dedup / now_fn.
6. ``await bus.subscribe("system.alerts", make_alert_handler(...))``.

Shutdown order (reverse, load-bearing):

7. ``await bus.close()`` — drain in-flight subscriptions before
   telegram_client.aclose() (publish-after-persist mirror).
8. ``await telegram_client.aclose()`` — close httpx connection pool.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from jinja2 import Environment, FileSystemLoader, select_autoescape

from packages.bus import NatsClient
from packages.observability import (
    configure,
    get_logger,
    make_metrics_asgi_app,
    make_registry,
)

from .config import Settings, load_alerts_config
from .consumer import make_alert_handler
from .dedup import DedupTracker
from .health import router as health_router
from .telegram import TelegramClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ["create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured :class:`FastAPI` for alerting-svc."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    configure(level=settings.log_level)
    logger = get_logger(settings.service_name, "system")
    registry_metrics = make_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Composition root for async resources. See module docstring."""
        # 1. Load + validate alerts.yaml (fail-fast on schema / env-var miss).
        alerts_config = load_alerts_config(Path(settings.alerts_yaml_path))

        # 2. Telegram client — DI-injected retry knobs from Settings per L-001.
        telegram_client = TelegramClient(
            token=settings.telegram_bot_token,
            channel_chat_ids=alerts_config.channel_chat_ids,
            max_retries=settings.alerting_max_retries,
            initial_backoff_s=settings.alerting_initial_backoff_s,
            logger=logger,
        )

        # 3. NATS bus.
        bus = NatsClient(
            servers=[settings.nats_url],
            name=settings.service_name,
            logger=logger,
        )
        await bus.connect()

        # 4. Dedup tracker (in-memory; reset on restart per OQ-4=A).
        dedup = DedupTracker(window_seconds=alerts_config.rate_limit.dedup_window_seconds)

        # 5. Jinja2 environment for template rendering. FileSystemLoader rooted
        # at parent of templates/ (templates referenced as `templates/foo.j2`
        # in alerts.yaml). autoescape=True for HTML safety.
        templates_root = Path(settings.alerts_yaml_path).parent
        jinja_env = Environment(
            loader=FileSystemLoader(str(templates_root)),
            autoescape=select_autoescape(["html", "xml", "j2"]),
            enable_async=False,
        )

        # 6. now_fn — injected per WG#5 for testability + §N1 UTC.
        now_fn = lambda: datetime.now(UTC)  # noqa: E731 — small lambda is clearer than def

        # 7. State attach.
        app.state.alerts_config = alerts_config
        app.state.telegram_client = telegram_client
        app.state.bus = bus
        app.state.dedup = dedup
        app.state.now_fn = now_fn

        # 8. Subscribe to system.alerts (per OQ-7=A — trading.events critical
        # filter is F5+). Subscribe failure crashes lifespan per fail-fast.
        handler = make_alert_handler(
            alerts_config=alerts_config,
            dedup=dedup,
            telegram_client=telegram_client,
            jinja_env=jinja_env,
            logger=logger,
            now_fn=now_fn,
        )
        await bus.subscribe("system.alerts", handler)

        logger.info(
            "service_started",
            http_port=settings.http_port,
            channels=sorted(alerts_config.channels),
            rules=len(alerts_config.rules),
            dedup_window_seconds=alerts_config.rate_limit.dedup_window_seconds,
        )
        try:
            yield
        finally:
            # Reverse shutdown: bus first (drain in-flight subs), telegram
            # second (close httpx pool).
            await bus.close()
            await telegram_client.aclose()
            logger.info("service_stopped")

    app = FastAPI(lifespan=lifespan)

    app.state.settings = settings
    app.state.logger = logger

    app.include_router(health_router)
    app.mount("/metrics", make_metrics_asgi_app(registry_metrics))
    return app
