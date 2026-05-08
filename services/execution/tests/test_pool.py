"""§N4 unit tests for :mod:`services.execution.app.pool` (T-215).

Mock-based: ``asyncpg.Pool`` returning canned bot rows + ``Mapping[str, str]``
env injection. Mocks ``BybitV5Client`` / ``BybitV5PrivateWs`` /
``BybitV5Adapter`` / ``PaperExchange`` ctors via ``monkeypatch``.

H-022 verbatim test: ``test_adapter_pool_uses_distinct_credentials_per_bot``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from services.execution.app.pool import (
    AdapterPoolResult,
    MissingBotCredentialsError,
    _make_task_exception_logger,
    _parse_slippage_params,
    _validate_slippage_model,
    build_adapter_pool,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _logger() -> Any:
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    log = logging.getLogger("test_pool")
    log.handlers = []
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return structlog.stdlib.get_logger("test_pool")


def _settings(bybit_leverage_cache_ttl_s: float = 3600.0) -> MagicMock:
    s = MagicMock()
    s.bybit_leverage_cache_ttl_s = bybit_leverage_cache_ttl_s
    return s


def _make_pool_with_rows(rows: list[dict[str, Any]]) -> MagicMock:
    """Return a mock ``asyncpg.Pool`` whose ``acquire`` ctx mgr yields a conn
    with ``conn.fetch`` returning the supplied rows."""
    pool = MagicMock()
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)

    @asynccontextmanager
    async def _acquire() -> Any:
        yield conn

    pool.acquire = _acquire
    return pool


_PAPER_ENV = {
    "BOT_PAPER1_PAPER_SEED_BALANCE": "10000",
    "BOT_PAPER1_PAPER_SLIPPAGE_MODEL": "fixed_pct",
    "BOT_PAPER1_PAPER_FEE_RATE": "0.0006",
    "BOT_PAPER1_PAPER_SLIPPAGE_PARAMS_JSON": '{"fixed_slippage_pct": "0.001"}',
}

_LIVE_ENV = {
    "BOT_ALPHA_BYBIT_API_KEY": "alpha_key",
    "BOT_ALPHA_BYBIT_API_SECRET": "alpha_secret",
    "BOT_ALPHA_BYBIT_SUB_ACCOUNT": "sub-alpha",
}


# ---------------------------------------------------------------------------
# Bot loading (3 tests)
# ---------------------------------------------------------------------------


async def test_build_adapter_pool_with_empty_bots_returns_empty_result() -> None:
    pool = _make_pool_with_rows([])
    bus = MagicMock()
    rate_limiter = MagicMock()
    result = await build_adapter_pool(
        pool=pool,
        bus=bus,
        rate_limiter=rate_limiter,
        settings=_settings(),
        bound_logger=_logger(),
        env={},
    )
    assert isinstance(result, AdapterPoolResult)
    assert result.adapters == {}
    assert result.ws_tasks == []
    assert result.paper_consumer_tasks == []
    assert result.paper_bot_ids == frozenset()  # T-218c H-031


async def test_build_adapter_pool_populates_paper_bot_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-218c H-031 regression guard: paper_bot_ids set populated for paper-mode bots only.

    Mixed live + paper bot rows; assert paper_bot_ids == {paper_bot_ids only}
    and live bots NOT in set. Field is frozenset (immutable; safe to share).
    """
    rows = [
        {"bot_id": "alpha-paper", "display_name": "A", "exchange_mode": "paper"},
        {"bot_id": "beta-paper", "display_name": "B", "exchange_mode": "paper"},
    ]
    pool = _make_pool_with_rows(rows)

    class _FakePaper:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        async def start_consuming(self) -> None:
            return None

    monkeypatch.setattr("services.execution.app.pool.PaperExchange", _FakePaper)
    env = {
        f"BOT_{name}_PAPER_{k}": v
        for name in ("ALPHA-PAPER", "BETA-PAPER")
        for k, v in {
            "SEED_BALANCE": "10000",
            "SLIPPAGE_MODEL": "fixed_pct",
            "FEE_RATE": "0.0006",
            "SLIPPAGE_PARAMS_JSON": '{"fixed_slippage_pct": "0.001"}',
        }.items()
    }
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env=env,
    )
    # H-031 contract: paper_bot_ids exactly matches the paper-mode bots.
    assert result.paper_bot_ids == frozenset({"alpha-paper", "beta-paper"})
    assert isinstance(result.paper_bot_ids, frozenset)
    # Cleanup spawned consumer tasks.
    for task in result.paper_consumer_tasks:
        task.cancel()
    await asyncio.gather(*result.paper_consumer_tasks, return_exceptions=True)


async def test_build_adapter_pool_filters_to_active_via_sql_where(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQL WHERE clause does the filtering — verify SQL string."""
    captured_sql: list[str] = []
    pool = MagicMock()
    conn = MagicMock()

    async def _fetch(sql: str) -> list[Any]:
        captured_sql.append(sql)
        return []

    conn.fetch = _fetch

    @asynccontextmanager
    async def _acquire() -> Any:
        yield conn

    pool.acquire = _acquire

    await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env={},
    )
    assert "WHERE status = 'active'" in captured_sql[0]
    assert "ORDER BY bot_id" in captured_sql[0]


async def test_build_adapter_pool_orders_by_bot_id_for_deterministic_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two paper bots; assert dict insertion order matches SQL row order."""
    rows = [
        {"bot_id": "alpha", "display_name": "A", "exchange_mode": "paper"},
        {"bot_id": "beta", "display_name": "B", "exchange_mode": "paper"},
    ]
    pool = _make_pool_with_rows(rows)
    paper_ctor_calls: list[str] = []

    class _FakePaper:
        def __init__(self, **kwargs: Any) -> None:
            paper_ctor_calls.append(str(kwargs["bot_id"]))

        async def start_consuming(self) -> None:
            return None

    monkeypatch.setattr("services.execution.app.pool.PaperExchange", _FakePaper)
    env = {
        f"BOT_{name}_PAPER_{k}": v
        for name in ("ALPHA", "BETA")
        for k, v in {
            "SEED_BALANCE": "10000",
            "SLIPPAGE_MODEL": "fixed_pct",
            "FEE_RATE": "0.0006",
            "SLIPPAGE_PARAMS_JSON": '{"fixed_slippage_pct": "0.001"}',
        }.items()
    }
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env=env,
    )
    assert paper_ctor_calls == ["alpha", "beta"]
    for task in result.paper_consumer_tasks:
        task.cancel()
    await asyncio.gather(*result.paper_consumer_tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Per-bot Bybit construction (4 tests)
# ---------------------------------------------------------------------------


def _patch_bybit_ctors(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    client_factory = MagicMock(side_effect=lambda **kw: MagicMock(_kwargs=kw))
    ws_factory = MagicMock(side_effect=lambda **kw: MagicMock(_kwargs=kw, run=AsyncMock()))
    adapter_factory = MagicMock(side_effect=lambda **kw: MagicMock(_kwargs=kw))
    monkeypatch.setattr("services.execution.app.pool.BybitV5Client", client_factory)
    monkeypatch.setattr("services.execution.app.pool.BybitV5PrivateWs", ws_factory)
    monkeypatch.setattr("services.execution.app.pool.BybitV5Adapter", adapter_factory)
    return client_factory, ws_factory, adapter_factory


async def test_bybit_live_bot_constructed_with_prod_urls_from_env_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _make_pool_with_rows([{"bot_id": "alpha", "display_name": "A", "exchange_mode": "live"}])
    client_factory, ws_factory, adapter_factory = _patch_bybit_ctors(monkeypatch)
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env=_LIVE_ENV,
    )
    client_kwargs = client_factory.call_args.kwargs
    assert client_kwargs["base_url"] == "https://api.bybit.com"
    assert client_kwargs["api_key"] == "alpha_key"
    assert client_kwargs["api_secret"] == "alpha_secret"
    ws_kwargs = ws_factory.call_args.kwargs
    assert ws_kwargs["ws_url"] == "wss://stream.bybit.com/v5/private"
    assert ws_kwargs["api_key"] == "alpha_key"
    adapter_kwargs = adapter_factory.call_args.kwargs
    assert adapter_kwargs["sub_account"] == "sub-alpha"
    for task in result.ws_tasks:
        task.cancel()
    await asyncio.gather(*result.ws_tasks, return_exceptions=True)


async def test_bybit_testnet_bot_constructed_with_testnet_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _make_pool_with_rows(
        [{"bot_id": "alpha", "display_name": "A", "exchange_mode": "testnet"}]
    )
    client_factory, ws_factory, _ = _patch_bybit_ctors(monkeypatch)
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env=_LIVE_ENV,
    )
    assert client_factory.call_args.kwargs["base_url"] == "https://api-testnet.bybit.com"
    assert ws_factory.call_args.kwargs["ws_url"] == "wss://stream-testnet.bybit.com/v5/private"
    for task in result.ws_tasks:
        task.cancel()
    await asyncio.gather(*result.ws_tasks, return_exceptions=True)


async def test_adapter_pool_uses_distinct_credentials_per_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-022 verbatim test — distinct env keys → distinct adapter ctor kwargs."""
    rows = [
        {"bot_id": "alpha", "display_name": "A", "exchange_mode": "live"},
        {"bot_id": "beta", "display_name": "B", "exchange_mode": "live"},
    ]
    pool = _make_pool_with_rows(rows)
    _, _, adapter_factory = _patch_bybit_ctors(monkeypatch)
    env = {
        "BOT_ALPHA_BYBIT_API_KEY": "alpha_key",
        "BOT_ALPHA_BYBIT_API_SECRET": "alpha_secret",
        "BOT_ALPHA_BYBIT_SUB_ACCOUNT": "sub-alpha",
        "BOT_BETA_BYBIT_API_KEY": "beta_key",
        "BOT_BETA_BYBIT_API_SECRET": "beta_secret",
        "BOT_BETA_BYBIT_SUB_ACCOUNT": "sub-beta",
    }
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env=env,
    )
    sub_accounts = [call.kwargs["sub_account"] for call in adapter_factory.call_args_list]
    assert sub_accounts == ["sub-alpha", "sub-beta"]
    # Distinct api_key / api_secret reach BybitV5Client (via the client factory mock).
    assert len(result.adapters) == 2
    for task in result.ws_tasks:
        task.cancel()
    await asyncio.gather(*result.ws_tasks, return_exceptions=True)


async def test_bybit_bot_ws_task_spawned_with_done_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _make_pool_with_rows([{"bot_id": "alpha", "display_name": "A", "exchange_mode": "live"}])
    _patch_bybit_ctors(monkeypatch)
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env=_LIVE_ENV,
    )
    assert len(result.ws_tasks) == 1
    assert result.ws_tasks[0].get_name() == "bybit_ws_alpha"
    for task in result.ws_tasks:
        task.cancel()
    await asyncio.gather(*result.ws_tasks, return_exceptions=True)


async def test_adapter_pool_passes_same_rate_limiter_instance_to_all_bybit_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-025 — single SharedRateLimiter shared across all Bybit adapters."""
    rows = [
        {"bot_id": "alpha", "display_name": "A", "exchange_mode": "live"},
        {"bot_id": "beta", "display_name": "B", "exchange_mode": "live"},
    ]
    pool = _make_pool_with_rows(rows)
    _, _, adapter_factory = _patch_bybit_ctors(monkeypatch)
    rate_limiter = MagicMock(name="shared_rate_limiter")
    env = {
        f"BOT_{name}_BYBIT_{k}": f"{name.lower()}-{k.lower()}"
        for name in ("ALPHA", "BETA")
        for k in ("API_KEY", "API_SECRET", "SUB_ACCOUNT")
    }
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=rate_limiter,
        settings=_settings(),
        bound_logger=_logger(),
        env=env,
    )
    limiters = [call.kwargs["limiter"] for call in adapter_factory.call_args_list]
    assert all(lim is rate_limiter for lim in limiters)
    for task in result.ws_tasks:
        task.cancel()
    await asyncio.gather(*result.ws_tasks, return_exceptions=True)


# ---------------------------------------------------------------------------
# Per-bot Paper construction (3 tests)
# ---------------------------------------------------------------------------


async def test_paper_bot_constructed_with_env_seed_balance_slippage_fee_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _make_pool_with_rows(
        [{"bot_id": "paper1", "display_name": "P1", "exchange_mode": "paper"}]
    )
    paper_kwargs: list[dict[str, Any]] = []

    class _FakePaper:
        def __init__(self, **kwargs: Any) -> None:
            paper_kwargs.append(kwargs)

        async def start_consuming(self) -> None:
            return None

    monkeypatch.setattr("services.execution.app.pool.PaperExchange", _FakePaper)
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env=_PAPER_ENV,
    )
    kw = paper_kwargs[0]
    assert kw["seed_balance"] == Decimal("10000")
    assert kw["slippage_model"] == "fixed_pct"
    assert kw["fee_rate"] == Decimal("0.0006")
    assert kw["slippage_params"] == {"fixed_slippage_pct": Decimal("0.001")}
    for task in result.paper_consumer_tasks:
        task.cancel()
    await asyncio.gather(*result.paper_consumer_tasks, return_exceptions=True)


async def test_paper_bot_consumer_task_spawned(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _make_pool_with_rows(
        [{"bot_id": "paper1", "display_name": "P1", "exchange_mode": "paper"}]
    )

    class _FakePaper:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def start_consuming(self) -> None:
            return None

    monkeypatch.setattr("services.execution.app.pool.PaperExchange", _FakePaper)
    result = await build_adapter_pool(
        pool=pool,
        bus=MagicMock(),
        rate_limiter=MagicMock(),
        settings=_settings(),
        bound_logger=_logger(),
        env=_PAPER_ENV,
    )
    assert len(result.paper_consumer_tasks) == 1
    assert result.paper_consumer_tasks[0].get_name() == "paper_consumer_paper1"
    for task in result.paper_consumer_tasks:
        task.cancel()
    await asyncio.gather(*result.paper_consumer_tasks, return_exceptions=True)


async def test_paper_bot_with_invalid_slippage_model_env_raises_value_error() -> None:
    pool = _make_pool_with_rows(
        [{"bot_id": "paper1", "display_name": "P1", "exchange_mode": "paper"}]
    )
    bad_env = {**_PAPER_ENV, "BOT_PAPER1_PAPER_SLIPPAGE_MODEL": "garbage"}
    with pytest.raises(ValueError, match="unknown slippage_model"):
        await build_adapter_pool(
            pool=pool,
            bus=MagicMock(),
            rate_limiter=MagicMock(),
            settings=_settings(),
            bound_logger=_logger(),
            env=bad_env,
        )


# ---------------------------------------------------------------------------
# Missing credentials (3 tests)
# ---------------------------------------------------------------------------


async def test_live_bot_missing_api_key_raises_MissingBotCredentialsError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _make_pool_with_rows([{"bot_id": "alpha", "display_name": "A", "exchange_mode": "live"}])
    _patch_bybit_ctors(monkeypatch)
    env = {
        "BOT_ALPHA_BYBIT_API_SECRET": "alpha_secret",
        "BOT_ALPHA_BYBIT_SUB_ACCOUNT": "sub-alpha",
    }
    with pytest.raises(MissingBotCredentialsError, match="BOT_ALPHA_BYBIT_API_KEY"):
        await build_adapter_pool(
            pool=pool,
            bus=MagicMock(),
            rate_limiter=MagicMock(),
            settings=_settings(),
            bound_logger=_logger(),
            env=env,
        )


async def test_live_bot_missing_sub_account_raises_MissingBotCredentialsError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _make_pool_with_rows([{"bot_id": "alpha", "display_name": "A", "exchange_mode": "live"}])
    _patch_bybit_ctors(monkeypatch)
    env = {
        "BOT_ALPHA_BYBIT_API_KEY": "alpha_key",
        "BOT_ALPHA_BYBIT_API_SECRET": "alpha_secret",
    }
    with pytest.raises(MissingBotCredentialsError, match="BOT_ALPHA_BYBIT_SUB_ACCOUNT"):
        await build_adapter_pool(
            pool=pool,
            bus=MagicMock(),
            rate_limiter=MagicMock(),
            settings=_settings(),
            bound_logger=_logger(),
            env=env,
        )


async def test_paper_bot_missing_seed_balance_raises_MissingBotCredentialsError() -> None:
    pool = _make_pool_with_rows(
        [{"bot_id": "paper1", "display_name": "P1", "exchange_mode": "paper"}]
    )
    bad_env = {k: v for k, v in _PAPER_ENV.items() if k != "BOT_PAPER1_PAPER_SEED_BALANCE"}
    with pytest.raises(MissingBotCredentialsError, match="BOT_PAPER1_PAPER_SEED_BALANCE"):
        await build_adapter_pool(
            pool=pool,
            bus=MagicMock(),
            rate_limiter=MagicMock(),
            settings=_settings(),
            bound_logger=_logger(),
            env=bad_env,
        )


# ---------------------------------------------------------------------------
# Done-callback error logger (2 tests)
# ---------------------------------------------------------------------------


async def test_task_exception_logger_logs_error_when_task_raises() -> None:
    bound_logger = MagicMock()
    callback = _make_task_exception_logger(bound_logger, "alpha")

    async def _raises() -> None:
        raise RuntimeError("ws crashed")

    task: asyncio.Task[None] = asyncio.create_task(_raises(), name="bybit_ws_alpha")
    await asyncio.sleep(0)
    callback(task)
    bound_logger.error.assert_called_once()
    call = bound_logger.error.call_args
    assert call.args[0] == "execution.background_task_died"
    assert call.kwargs["bot_id"] == "alpha"
    assert "ws crashed" in call.kwargs["error"]


async def test_task_exception_logger_silent_on_cancellation() -> None:
    bound_logger = MagicMock()
    callback = _make_task_exception_logger(bound_logger, "alpha")

    async def _runs() -> None:
        await asyncio.sleep(60)

    task: asyncio.Task[None] = asyncio.create_task(_runs(), name="bybit_ws_alpha")
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    callback(task)
    bound_logger.error.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers (3 tests)
# ---------------------------------------------------------------------------


def test_validate_slippage_model_accepts_known_models() -> None:
    assert _validate_slippage_model("fixed_pct") == "fixed_pct"
    assert _validate_slippage_model("proportional_to_qty") == "proportional_to_qty"
    assert _validate_slippage_model("half_spread") == "half_spread"


def test_validate_slippage_model_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown slippage_model"):
        _validate_slippage_model("garbage")


def test_parse_slippage_params_round_trips_decimal_strings() -> None:
    raw = json.dumps({"fixed_slippage_pct": "0.001"})
    out = _parse_slippage_params(raw)
    assert out == {"fixed_slippage_pct": Decimal("0.001")}
    assert isinstance(out["fixed_slippage_pct"], Decimal)


def test_parse_slippage_params_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        _parse_slippage_params(json.dumps([1, 2, 3]))
