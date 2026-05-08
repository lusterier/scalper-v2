"""§9.5 adapter pool composition root (T-215).

Reads active bots from the ``bots`` table (status='active'); per-bot
constructs the appropriate :class:`packages.exchange.protocols.ExchangeClient`
adapter (BybitV5Adapter for live/testnet, PaperExchange for paper);
spawns long-lived background tasks (BybitV5PrivateWs.run for live,
PaperExchange.start_consuming for paper). Returns the composed
:class:`AdapterPoolResult` for lifespan ownership.

Per H-022, all per-bot Bybit credentials live as env vars
``BOT_<ID>_BYBIT_API_KEY/SECRET/SUB_ACCOUNT`` (per ADR-0004 — supersedes
ADR-0003 §Decision 6 sub_account-from-column path). Paper-bot params
live as env vars ``BOT_<ID>_PAPER_SEED_BALANCE/SLIPPAGE_MODEL/FEE_RATE/SLIPPAGE_PARAMS_JSON``
per OQ-2 (deferred from bot YAML config to env at F2 scope; F5+ may
revisit when strategy-engine consumes YAML configs).

Lookup pattern: ``bots.bot_id`` is stored lowercase in DB; env vars use
``bot_id.upper()`` per H-022 family convention. Example: bot row
``bot_id='alpha'`` reads env ``BOT_ALPHA_BYBIT_API_KEY``.

Bybit URL routing per ``exchange_mode``:

* ``live`` → REST ``https://api.bybit.com`` + WS ``wss://stream.bybit.com/v5/private``
* ``testnet`` → REST ``https://api-testnet.bybit.com`` + WS ``wss://stream-testnet.bybit.com/v5/private``
* ``paper`` → in-process simulator, no URLs

Demo deployment (per memory ``deployment.md``) uses ``exchange_mode='live'``
with a demo-flagged ``BOT_<ID>_BYBIT_SUB_ACCOUNT`` env value; live URLs
are the contracted F2 routing for demo sub-accounts (ADR-0004).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from packages.core import BotId
from packages.db.queries.execution import select_active_bots
from packages.exchange.bybit_v5 import BybitV5Adapter, BybitV5Client, BybitV5PrivateWs
from packages.exchange.paper import PaperExchange

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import asyncpg
    from structlog.stdlib import BoundLogger

    from packages.bus import NatsClient
    from packages.db.queries.execution import BotRow
    from packages.exchange.paper.adapter import SlippageModel
    from packages.exchange.protocols import ExchangeClient
    from packages.exchange.rate_limiter import SharedRateLimiter

    from .config import Settings


__all__ = [
    "AdapterPoolResult",
    "MissingBotCredentialsError",
    "build_adapter_pool",
]


# Bybit URL constants — protocol-binding per L-001 (Bybit-published spec values,
# NOT operationally tunable via Settings). Mirror T-207 _RECV_WINDOW_MS / T-209
# _BACKOFF_BASE_S precedent.
_BYBIT_PROD_REST_URL = "https://api.bybit.com"
_BYBIT_TESTNET_REST_URL = "https://api-testnet.bybit.com"
_BYBIT_PROD_WS_URL = "wss://stream.bybit.com/v5/private"
_BYBIT_TESTNET_WS_URL = "wss://stream-testnet.bybit.com/v5/private"

_VALID_SLIPPAGE_MODELS: frozenset[str] = frozenset(
    {"fixed_pct", "proportional_to_qty", "half_spread"}
)


class MissingBotCredentialsError(RuntimeError):
    """Raised at composition when a required env var is missing for a bot.

    Lifespan crashes BEFORE uvicorn binds the port; mirrors Settings
    startup-validation invariant. Operator must set the env var and
    restart.
    """


@dataclass(frozen=True, slots=True)
class AdapterPoolResult:
    """Composition result owned by lifespan; attached to ``app.state``."""

    adapters: dict[BotId, ExchangeClient]
    ws_tasks: list[asyncio.Task[None]]
    paper_consumer_tasks: list[asyncio.Task[None]]


def _require_env(env: Mapping[str, str], key: str) -> str:
    """Return ``env[key]`` or raise :class:`MissingBotCredentialsError` with the key name."""
    value = env.get(key)
    if value is None or value == "":
        raise MissingBotCredentialsError(f"Missing env var: {key}")
    return value


def _validate_slippage_model(value: str) -> SlippageModel:
    if value not in _VALID_SLIPPAGE_MODELS:
        raise ValueError(
            f"unknown slippage_model {value!r}; expected one of {sorted(_VALID_SLIPPAGE_MODELS)}"
        )
    return value  # type: ignore[return-value]


def _parse_slippage_params(raw: str) -> dict[str, Decimal]:
    """Decode JSON object → ``{str: Decimal}``; raise on shape mismatch."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"slippage_params must be JSON object, got {type(data).__name__}")
    return {str(k): Decimal(str(v)) for k, v in data.items()}


def _make_task_exception_logger(
    bound_logger: BoundLogger,
    bot_id: str,
) -> Callable[[asyncio.Task[None]], None]:
    """Done-callback factory: log ERROR on task failure; silent on cancellation."""

    def _log(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            bound_logger.error(
                "execution.background_task_died",
                bot_id=bot_id,
                task_name=task.get_name(),
                error=str(exc),
            )

    return _log


def _construct_bybit_adapter(
    *,
    bot_row: BotRow,
    env: Mapping[str, str],
    bus: NatsClient,
    rate_limiter: SharedRateLimiter,
    settings: Settings,
    bound_logger: BoundLogger,
) -> tuple[BybitV5Adapter, asyncio.Task[None]]:
    bot_prefix = f"BOT_{bot_row.bot_id.upper()}"
    api_key = _require_env(env, f"{bot_prefix}_BYBIT_API_KEY")
    api_secret = _require_env(env, f"{bot_prefix}_BYBIT_API_SECRET")
    sub_account = _require_env(env, f"{bot_prefix}_BYBIT_SUB_ACCOUNT")
    if bot_row.exchange_mode == "testnet":
        rest_url = _BYBIT_TESTNET_REST_URL
        ws_url = _BYBIT_TESTNET_WS_URL
    else:
        rest_url = _BYBIT_PROD_REST_URL
        ws_url = _BYBIT_PROD_WS_URL
    client = BybitV5Client(api_key=api_key, api_secret=api_secret, base_url=rest_url)
    ws = BybitV5PrivateWs(
        api_key=api_key,
        api_secret=api_secret,
        ws_url=ws_url,
        logger=bound_logger,
    )
    adapter = BybitV5Adapter(
        client=client,
        ws=ws,
        limiter=rate_limiter,
        bus=bus,
        sub_account=sub_account,
        leverage_cache_ttl_s=settings.bybit_leverage_cache_ttl_s,
    )
    ws_task = asyncio.create_task(ws.run(), name=f"bybit_ws_{bot_row.bot_id}")
    ws_task.add_done_callback(_make_task_exception_logger(bound_logger, bot_row.bot_id))
    return adapter, ws_task


def _construct_paper_adapter(
    *,
    bot_row: BotRow,
    env: Mapping[str, str],
    bus: NatsClient,
    pool: asyncpg.Pool,
    bound_logger: BoundLogger,
) -> tuple[PaperExchange, asyncio.Task[None]]:
    bot_prefix = f"BOT_{bot_row.bot_id.upper()}_PAPER"
    seed_balance = Decimal(_require_env(env, f"{bot_prefix}_SEED_BALANCE"))
    slippage_model = _validate_slippage_model(_require_env(env, f"{bot_prefix}_SLIPPAGE_MODEL"))
    fee_rate = Decimal(_require_env(env, f"{bot_prefix}_FEE_RATE"))
    slippage_params = _parse_slippage_params(
        _require_env(env, f"{bot_prefix}_SLIPPAGE_PARAMS_JSON")
    )
    adapter = PaperExchange(
        seed_balance=seed_balance,
        slippage_model=slippage_model,
        fee_rate=fee_rate,
        bot_id=BotId(bot_row.bot_id),
        bus=bus,
        slippage_params=slippage_params,
        pool=pool,
        # T-511b2 / ADR-0010: primary bot PE emits TradeClosedPayload on
        # paper-side close → ShadowWorker._on_parent_close cancels variants
        # (H-016 hook). Variant PE in shadow_worker._run_shadow_variant
        # keeps default False (avoids self-cancel loop).
        emit_parent_lifecycle=True,
    )
    consumer_task = asyncio.create_task(
        adapter.start_consuming(),
        name=f"paper_consumer_{bot_row.bot_id}",
    )
    consumer_task.add_done_callback(_make_task_exception_logger(bound_logger, bot_row.bot_id))
    return adapter, consumer_task


async def build_adapter_pool(
    *,
    pool: asyncpg.Pool,
    bus: NatsClient,
    rate_limiter: SharedRateLimiter,
    settings: Settings,
    bound_logger: BoundLogger,
    env: Mapping[str, str] | None = None,
) -> AdapterPoolResult:
    """Read active bots; per-bot construct adapter + spawn background task.

    Per OQ-7 default A: missing env credentials raise
    :class:`MissingBotCredentialsError` at the bot's construction iteration;
    lifespan crashes before completing other bots. ``ORDER BY bot_id`` in
    the underlying SQL query (Decision #6) ensures deterministic
    partial-failure attribution across restarts.
    """
    resolved_env: Mapping[str, str] = env if env is not None else os.environ
    async with pool.acquire() as conn:
        bot_rows = await select_active_bots(conn)
    adapters: dict[BotId, ExchangeClient] = {}
    ws_tasks: list[asyncio.Task[None]] = []
    paper_consumer_tasks: list[asyncio.Task[None]] = []
    for bot_row in bot_rows:
        if bot_row.exchange_mode in ("live", "testnet"):
            adapter, ws_task = _construct_bybit_adapter(
                bot_row=bot_row,
                env=resolved_env,
                bus=bus,
                rate_limiter=rate_limiter,
                settings=settings,
                bound_logger=bound_logger,
            )
            adapters[BotId(bot_row.bot_id)] = adapter
            ws_tasks.append(ws_task)
        else:
            paper_adapter, consumer_task = _construct_paper_adapter(
                bot_row=bot_row,
                env=resolved_env,
                bus=bus,
                pool=pool,
                bound_logger=bound_logger,
            )
            adapters[BotId(bot_row.bot_id)] = paper_adapter
            paper_consumer_tasks.append(consumer_task)
    bound_logger.info(
        "execution.adapter_pool_built",
        bots_loaded=len(adapters),
        ws_tasks=len(ws_tasks),
        paper_consumer_tasks=len(paper_consumer_tasks),
    )
    return AdapterPoolResult(
        adapters=adapters,
        ws_tasks=ws_tasks,
        paper_consumer_tasks=paper_consumer_tasks,
    )
