"""§N5 unit tests for :mod:`packages.scoring.resolver` (T-306).

TDD discipline (§N4 spirit per WG#3 T-200 precedent): tests written
before implementation. Mock-based: NatsClient.kv_get + asyncpg.Pool +
select_latest_feature patched.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.bus.errors import NotConnectedError
from packages.bus.schemas import FeatureUpdate
from packages.bus.schemas.signals import SignalValidated
from packages.db.queries.feature_engine import LatestFeatureRow
from packages.features.types import FeatureValue
from packages.scoring import resolver as resolver_mod
from packages.scoring.resolver import (
    FeatureResolver,
    parse_feature_ref,
    substitute_template,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _signal(symbol: str = "BTCUSDT") -> SignalValidated:
    return SignalValidated(
        source="webhook",
        idempotency_key="test-key-1",
        received_at=_FIXED_NOW,
        symbol=symbol,
        original_symbol=symbol,
        action="LONG",
        expires_at=_FIXED_NOW + timedelta(seconds=60),
        payload={},
    )


def _build_pool() -> MagicMock:
    pool = MagicMock()
    fake_conn = MagicMock()

    @asynccontextmanager
    async def _acquire() -> AsyncIterator[MagicMock]:
        yield fake_conn

    pool.acquire = _acquire
    return pool


def _build_resolver(
    *,
    bus: MagicMock | None = None,
    pool: MagicMock | None = None,
    now_fn_value: datetime = _FIXED_NOW,
) -> FeatureResolver:
    if bus is None:
        bus = MagicMock()
        bus.kv_get = AsyncMock(return_value=None)
    if pool is None:
        pool = _build_pool()
    return FeatureResolver(
        bus=bus,
        pool=pool,
        bound_logger=MagicMock(),
        now_fn=lambda: now_fn_value,
    )


def _make_kv_payload(
    *,
    feature_name: str = "ind.btcusdt.15m.ema_20",
    symbol: str = "btcusdt",
    value_num: float | None = 50000.5,
    value_bool: bool | None = None,
    value_json: dict[str, object] | None = None,
    computed_at: datetime = _FIXED_NOW,
) -> bytes:
    update = FeatureUpdate(
        feature_name=feature_name,
        symbol=symbol,
        computed_at=computed_at,
        value_num=value_num,
        value_bool=value_bool,
        value_json=value_json,
        source_version="v1",
    )
    return update.model_dump_json().encode()


# region: substitute_template ------------------------------------------------


def test_substitute_template_happy_path_lowercases_symbol() -> None:
    result = substitute_template("ind.${signal.symbol}.15m.ema_20", _signal("BTCUSDT"))
    assert result == "ind.btcusdt.15m.ema_20"


def test_substitute_template_unsupported_template_raises() -> None:
    with pytest.raises(ValueError, match="unsupported template"):
        substitute_template("foo.${signal.unknown}", _signal())


def test_substitute_template_no_template_passthrough() -> None:
    result = substitute_template("static.feature.name", _signal())
    assert result == "static.feature.name"


# region: parse_feature_ref -------------------------------------------------


def test_parse_feature_ref_happy_path_4_segments() -> None:
    symbol, interval = parse_feature_ref("ind.btcusdt.15m.ema_20")
    assert symbol == "btcusdt"
    assert interval == "15m"


def test_parse_feature_ref_unknown_interval_raises() -> None:
    with pytest.raises(ValueError, match="unknown interval"):
        parse_feature_ref("ind.btcusdt.2m.ema_20")


def test_parse_feature_ref_too_few_segments_raises() -> None:
    with pytest.raises(ValueError, match="malformed feature-ref"):
        parse_feature_ref("ema_20")


# region: FeatureResolver — KV path ------------------------------------------


async def test_resolve_kv_hit_returns_ok() -> None:
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(_make_kv_payload(), 1))
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.status == "ok"
    assert result.value == FeatureValue(value_num=Decimal("50000.5"))
    assert result.error_info is None


async def test_resolve_kv_decode_fail_falls_through_to_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(b"not-json-bytes", 1))
    db_mock = AsyncMock(
        return_value=LatestFeatureRow(
            value_num=42.0, value_bool=None, value_json=None, computed_at=_FIXED_NOW
        )
    )
    monkeypatch.setattr(resolver_mod, "select_latest_feature", db_mock)
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.status == "ok"
    assert result.value == FeatureValue(value_num=Decimal("42.0"))


async def test_resolve_kv_value_bool_variant() -> None:
    bus = MagicMock()
    bus.kv_get = AsyncMock(
        return_value=(
            _make_kv_payload(value_num=None, value_bool=True),
            1,
        )
    )
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.flag", signal=_signal())
    assert result.status == "ok"
    assert result.value == FeatureValue(value_bool=True)


async def test_resolve_kv_value_json_variant() -> None:
    bus = MagicMock()
    bus.kv_get = AsyncMock(
        return_value=(
            _make_kv_payload(value_num=None, value_json={"upper": 100.5, "lower": 99.5}),
            1,
        )
    )
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.bb", signal=_signal())
    assert result.status == "ok"
    assert result.value == FeatureValue(value_json={"upper": 100.5, "lower": 99.5})


async def test_resolve_kv_bus_error_falls_through_to_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = MagicMock()
    bus.kv_get = AsyncMock(side_effect=NotConnectedError("not connected"))
    db_mock = AsyncMock(
        return_value=LatestFeatureRow(
            value_num=1.5, value_bool=None, value_json=None, computed_at=_FIXED_NOW
        )
    )
    monkeypatch.setattr(resolver_mod, "select_latest_feature", db_mock)
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.status == "ok"
    assert result.value == FeatureValue(value_num=Decimal("1.5"))


# region: FeatureResolver — DB fallback --------------------------------------


async def test_resolve_kv_miss_db_hit_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    db_mock = AsyncMock(
        return_value=LatestFeatureRow(
            value_num=99.99, value_bool=None, value_json=None, computed_at=_FIXED_NOW
        )
    )
    monkeypatch.setattr(resolver_mod, "select_latest_feature", db_mock)
    r = _build_resolver()
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.status == "ok"
    assert result.value == FeatureValue(value_num=Decimal("99.99"))


async def test_resolve_kv_miss_db_miss_returns_data_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(resolver_mod, "select_latest_feature", db_mock)
    r = _build_resolver()
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.status == "data_missing"
    assert result.value is None
    assert result.error_info is not None
    assert "kv+db missing" in str(result.error_info)


# region: staleness check ---------------------------------------------------


async def test_resolve_kv_stale_returns_data_stale() -> None:
    """KV value with computed_at older than 2 * 15m = 1800s default → data_stale."""
    stale_computed_at = _FIXED_NOW - timedelta(seconds=2000)
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(_make_kv_payload(computed_at=stale_computed_at), 1))
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.status == "data_stale"
    assert result.value is None
    assert result.error_info is not None
    assert result.error_info["age_sec"] == 2000
    assert result.error_info["max_staleness_sec"] == 1800


async def test_resolve_db_stale_returns_data_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    stale_computed_at = _FIXED_NOW - timedelta(seconds=2000)
    db_mock = AsyncMock(
        return_value=LatestFeatureRow(
            value_num=1.0, value_bool=None, value_json=None, computed_at=stale_computed_at
        )
    )
    monkeypatch.setattr(resolver_mod, "select_latest_feature", db_mock)
    r = _build_resolver()
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.status == "data_stale"


async def test_resolve_default_staleness_15m_is_1800s() -> None:
    """15m interval → max_staleness_sec = 2 * 900 = 1800s; age=1700 → ok."""
    fresh_computed_at = _FIXED_NOW - timedelta(seconds=1700)
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(_make_kv_payload(computed_at=fresh_computed_at), 1))
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.status == "ok"


async def test_resolve_default_staleness_1h_is_7200s() -> None:
    """1h interval → max_staleness_sec = 2 * 3600 = 7200s; age=7000 → ok."""
    fresh_computed_at = _FIXED_NOW - timedelta(seconds=7000)
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(_make_kv_payload(computed_at=fresh_computed_at), 1))
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.1h.rsi_14", signal=_signal())
    assert result.status == "ok"


async def test_resolve_max_staleness_zero_triggers_stale_on_age_one_sec() -> None:
    """max_staleness_sec=0 → any non-zero age triggers data_stale."""
    aged_computed_at = _FIXED_NOW - timedelta(seconds=1)
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(_make_kv_payload(computed_at=aged_computed_at), 1))
    r = _build_resolver(bus=bus)
    result = await r.resolve(
        rule_feature="ind.${signal.symbol}.15m.ema_20",
        signal=_signal(),
        max_staleness_sec=0,
    )
    assert result.status == "data_stale"


async def test_resolve_max_staleness_override_per_call() -> None:
    """max_staleness_sec override beats interval-derived default."""
    aged_computed_at = _FIXED_NOW - timedelta(seconds=100)
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(_make_kv_payload(computed_at=aged_computed_at), 1))
    r = _build_resolver(bus=bus)
    # Default 15m would allow 1800s; override to 50s → 100 > 50 → stale.
    result = await r.resolve(
        rule_feature="ind.${signal.symbol}.15m.ema_20",
        signal=_signal(),
        max_staleness_sec=50,
    )
    assert result.status == "data_stale"


# region: Decimal precision via str-conversion -------------------------------


async def test_resolve_value_num_decimal_via_str_preserves_representation() -> None:
    """FeatureUpdate.value_num: float → FeatureValue.value_num: Decimal(str(float))."""
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=(_make_kv_payload(value_num=1.0001), 1))
    r = _build_resolver(bus=bus)
    result = await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal())
    assert result.value == FeatureValue(value_num=Decimal("1.0001"))


# region: contract pins (KV key + DB call args) -----------------------------


async def test_resolve_kv_key_uses_uppercase_symbol_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Producer at services/feature_engine/app/pipeline.py:239 writes KV key as
    `f"{feature_name}:{candle.symbol}"` where candle.symbol is canonical UPPER.
    Resolver must reconstruct UPPER suffix to hit producer-written KV entries.
    """
    bus = MagicMock()
    bus.kv_get = AsyncMock(return_value=None)
    monkeypatch.setattr(resolver_mod, "select_latest_feature", AsyncMock(return_value=None))
    r = _build_resolver(bus=bus)
    await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal("BTCUSDT"))
    bus.kv_get.assert_awaited_once_with("feature_latest", "ind.btcusdt.15m.ema_20:BTCUSDT")


async def test_resolve_db_lookup_uses_resolved_ref_and_lowercase_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB select_latest_feature uses resolved feature_name + lowercased symbol from feature-ref."""
    db_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(resolver_mod, "select_latest_feature", db_mock)
    r = _build_resolver()
    await r.resolve(rule_feature="ind.${signal.symbol}.15m.ema_20", signal=_signal("BTCUSDT"))
    db_mock.assert_awaited_once()
    await_args = db_mock.await_args
    assert await_args is not None
    assert await_args.kwargs["feature_name"] == "ind.btcusdt.15m.ema_20"
    assert await_args.kwargs["symbol"] == "btcusdt"


# ---------------------------------------------------------------------------
# T-520 sub-commit #2 — resolve_history (T-306 series + plugin upgrade)
# ---------------------------------------------------------------------------


async def test_resolve_history_returns_chronological_feature_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_history reads from DB select_feature_history; oldest → newest order preserved."""
    older = _FIXED_NOW - timedelta(minutes=30)
    middle = _FIXED_NOW - timedelta(minutes=15)
    rows = [
        LatestFeatureRow(value_num=100.0, value_bool=None, value_json=None, computed_at=older),
        LatestFeatureRow(value_num=101.0, value_bool=None, value_json=None, computed_at=middle),
        LatestFeatureRow(value_num=102.0, value_bool=None, value_json=None, computed_at=_FIXED_NOW),
    ]
    history_mock = AsyncMock(return_value=rows)
    monkeypatch.setattr(resolver_mod, "select_feature_history", history_mock)
    r = _build_resolver()
    history = await r.resolve_history(
        rule_feature="ind.${signal.symbol}.15m.ema_20",
        signal=_signal("BTCUSDT"),
        n_samples=3,
    )
    assert len(history) == 3
    expected_values = [Decimal("100.0"), Decimal("101.0"), Decimal("102.0")]
    assert [fv.value_num for fv in history] == expected_values
    history_mock.assert_awaited_once()
    assert history_mock.await_args is not None
    kwargs = history_mock.await_args.kwargs
    assert kwargs["feature_name"] == "ind.btcusdt.15m.ema_20"
    assert kwargs["symbol"] == "btcusdt"
    assert kwargs["n_samples"] == 3


async def test_resolve_history_empty_on_db_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """DB miss → empty list (caller treats as data_missing per series condition contract)."""
    history_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(resolver_mod, "select_feature_history", history_mock)
    r = _build_resolver()
    result = await r.resolve_history(
        rule_feature="ind.${signal.symbol}.15m.ema_20",
        signal=_signal("BTCUSDT"),
        n_samples=10,
    )
    assert result == []
