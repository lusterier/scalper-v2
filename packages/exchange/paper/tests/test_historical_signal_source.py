"""§N4 unit tests for :mod:`packages.exchange.paper.historical_signal_source` (T-504).

Mock-based: ``asyncpg.Pool`` + ``conn.transaction()`` + ``conn.cursor(...)``
async iterator stack simulated. Test fixtures hand-computed match T-504
plan-doc §A-§H verification examples.

8 tests covering:

* §A chronological order — 3 rows yield in ASC order.
* §B symbol_universe filter binds to ``$1`` (ANY array).
* §C half-open time window binds to ``$2 / $3`` (>= from_at, < to_at).
* §D SQL hardcodes ``ingestion_status = 'validated'`` literal.
* §E empty ``symbol_universe`` → ``ValueError`` at construction.
* boundary — ``to_at == from_at`` and ``to_at < from_at`` both raise (parametrize).
* §F empty cursor → empty iteration, no error.
* §G payload-as-str decoded via ``json.loads`` to dict (no-codec path).
* §H payload-as-dict passes through unchanged (codec-registered path).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from packages.core.types import Action, IngestionStatus
from packages.exchange.paper.historical_signal_source import HistoricalSignalSource

_FROM_AT = datetime(2026, 4, 1, tzinfo=UTC)
_TO_AT = datetime(2026, 5, 1, tzinfo=UTC)


def _make_signal_row(
    *,
    row_id: int = 1,
    received_at: datetime | None = None,
    symbol: str = "BTCUSDT",
    action: str = "LONG",
    payload: dict[str, Any] | str | None = None,
    ingestion_status: str = "validated",
) -> dict[str, Any]:
    """Helper: build mock asyncpg row dict for signals SELECT."""
    return {
        "id": row_id,
        "received_at": received_at or _FROM_AT + timedelta(hours=row_id),
        "schema_version": "1.0",
        "source": "tv_test",
        "idempotency_key": f"key-{row_id}",
        "symbol": symbol,
        "original_symbol": None,
        "action": action,
        "payload": payload if payload is not None else {"action": action},
        "ingestion_status": ingestion_status,
        "correlation_id": f"corr-{row_id}",
    }


class _MockCursor:
    """Mock asyncpg cursor — async iterator over canned rows + records bind args."""

    def __init__(self, rows: list[dict[str, Any]], captured: dict[str, Any]) -> None:
        self._rows = rows
        self._captured = captured

    def __call__(
        self,
        sql: str,
        *args: Any,
        prefetch: int | None = None,
    ) -> _MockCursor:
        self._captured["sql"] = sql
        self._captured["args"] = args
        self._captured["prefetch"] = prefetch
        return self

    def __aiter__(self) -> _MockCursor:
        self._iter = iter(self._rows)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


def _make_pool(rows: list[dict[str, Any]], captured: dict[str, Any]) -> MagicMock:
    """Build mock asyncpg.Pool whose acquire().transaction().cursor(...) yields rows."""
    cursor_callable = _MockCursor(rows, captured)

    @asynccontextmanager
    async def _tx_cm() -> Any:
        yield None

    conn = MagicMock()
    conn.cursor = cursor_callable
    conn.transaction = _tx_cm

    @asynccontextmanager
    async def _acquire_cm() -> Any:
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire_cm
    return pool


async def test_yields_rows_chronologically() -> None:
    """§A — 3 rows in DB-returned ASC order yield in same order."""
    captured: dict[str, Any] = {}
    rows = [
        _make_signal_row(row_id=1, received_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC)),
        _make_signal_row(row_id=2, received_at=datetime(2026, 4, 1, 12, 5, tzinfo=UTC)),
        _make_signal_row(row_id=3, received_at=datetime(2026, 4, 1, 13, 0, tzinfo=UTC)),
    ]
    pool = _make_pool(rows, captured)
    source = HistoricalSignalSource(
        pool,
        bot_id="alpha",
        symbol_universe=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    yielded = [signal async for signal in source]
    assert [s.id for s in yielded] == [1, 2, 3]
    assert all(s.symbol == "BTCUSDT" for s in yielded)
    assert all(s.action == Action.LONG for s in yielded)


async def test_filters_by_symbol_universe_via_bind_args() -> None:
    """§B — symbol_universe binds to $1 (ANY array)."""
    captured: dict[str, Any] = {}
    pool = _make_pool([], captured)
    source = HistoricalSignalSource(
        pool,
        bot_id="alpha",
        symbol_universe=["BTCUSDT", "ETHUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    _ = [signal async for signal in source]
    assert captured["args"][0] == ["BTCUSDT", "ETHUSDT"]


async def test_time_window_half_open_interval() -> None:
    """§C — from_at + to_at bind to $2 + $3 (>= from_at, < to_at)."""
    captured: dict[str, Any] = {}
    pool = _make_pool([], captured)
    source = HistoricalSignalSource(
        pool,
        bot_id="alpha",
        symbol_universe=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    _ = [signal async for signal in source]
    assert captured["args"][1] == _FROM_AT
    assert captured["args"][2] == _TO_AT


async def test_ingestion_status_filter_in_sql() -> None:
    """§D — SQL hardcodes ingestion_status = 'validated' literal."""
    captured: dict[str, Any] = {}
    pool = _make_pool([], captured)
    source = HistoricalSignalSource(
        pool,
        bot_id="alpha",
        symbol_universe=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    _ = [signal async for signal in source]
    assert "ingestion_status = 'validated'" in captured["sql"]
    assert "ORDER BY received_at ASC" in captured["sql"]


def test_empty_symbol_universe_raises_value_error() -> None:
    """§E — empty symbol_universe raises ValueError at construction."""
    pool = MagicMock()
    with pytest.raises(ValueError, match="symbol_universe must not be empty"):
        HistoricalSignalSource(
            pool,
            bot_id="alpha",
            symbol_universe=[],
            from_at=_FROM_AT,
            to_at=_TO_AT,
        )


@pytest.mark.parametrize(
    ("from_at", "to_at"),
    [
        (_FROM_AT, _FROM_AT),  # to_at == from_at
        (_TO_AT, _FROM_AT),  # to_at < from_at
    ],
)
def test_to_at_not_after_from_at_raises_value_error(from_at: datetime, to_at: datetime) -> None:
    """Boundary — to_at must be strictly > from_at."""
    pool = MagicMock()
    with pytest.raises(ValueError, match="to_at must be > from_at"):
        HistoricalSignalSource(
            pool,
            bot_id="alpha",
            symbol_universe=["BTCUSDT"],
            from_at=from_at,
            to_at=to_at,
        )


async def test_empty_result_yields_zero_rows() -> None:
    """§F — cursor yields zero rows; iterator completes cleanly."""
    captured: dict[str, Any] = {}
    pool = _make_pool([], captured)
    source = HistoricalSignalSource(
        pool,
        bot_id="alpha",
        symbol_universe=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    yielded = [signal async for signal in source]
    assert yielded == []


@pytest.mark.parametrize(
    ("payload_input", "label"),
    [
        (json.dumps({"action": "LONG", "qty": "0.001"}), "str-no-codec"),
        ({"action": "LONG", "qty": "0.001"}, "dict-codec"),
    ],
)
async def test_payload_decoded_to_dict_regardless_of_codec_path(
    payload_input: dict[str, Any] | str, label: str
) -> None:
    """§G + §H — payload str (no-codec) and dict (codec) both land as dict (L-011)."""
    captured: dict[str, Any] = {}
    rows = [_make_signal_row(payload=payload_input)]
    pool = _make_pool(rows, captured)
    source = HistoricalSignalSource(
        pool,
        bot_id="alpha",
        symbol_universe=["BTCUSDT"],
        from_at=_FROM_AT,
        to_at=_TO_AT,
    )
    yielded = [signal async for signal in source]
    assert len(yielded) == 1
    assert isinstance(yielded[0].payload, dict)
    assert yielded[0].payload == {"action": "LONG", "qty": "0.001"}
    assert yielded[0].ingestion_status == IngestionStatus.VALIDATED
