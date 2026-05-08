"""T-506 PaperExchange replay-mode tests.

§N4 TDD: 12 unit tests covering replay-mode constructor + lifecycle +
intra-candle expansion correctness + BLOCKER guard for ``_last_candle``
cache parity. Hand verification fixtures cross-check T-505 generator
outputs (per docs/plans/T-506.md §A-§D worked examples).

Live-mode test suite at ``test_adapter.py`` + ``test_adapter_fill_semantics.py``
is the regression guard for backwards-compat (constructor defaults
``mode='live'`` + ``historical_source=None``); those tests run unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.core import BotId
from packages.exchange.paper import PaperExchange
from packages.exchange.paper.historical_ohlc_source import OHLCRow

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from packages.exchange.paper.adapter import PendingSLTPFill


# --- Test fixtures ---------------------------------------------------------


def _make_pool() -> MagicMock:
    """asyncpg.Pool stand-in (mirror test_adapter_fill_semantics.py pattern)."""
    pool = MagicMock()
    pool.close = AsyncMock()
    conn = MagicMock()
    fake_row = {"id": 1}
    conn.fetchrow = AsyncMock(return_value=fake_row)
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetch = AsyncMock(return_value=[])
    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock(return_value=conn)
    tx_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx_cm)
    pool_cm = MagicMock()
    pool_cm.__aenter__ = AsyncMock(return_value=conn)
    pool_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=pool_cm)
    return pool


class _FakeHistoricalSource:
    """Async-iterable test double matching HistoricalOHLCSource interface.

    Yields the supplied OHLCRow list in order; tracks iteration count.
    """

    def __init__(self, rows: list[OHLCRow]) -> None:
        self._rows = rows
        self.iter_count = 0

    async def __aiter__(self) -> AsyncIterator[OHLCRow]:
        for row in self._rows:
            self.iter_count += 1
            yield row


def _make_replay_paper(
    rows: list[OHLCRow],
    *,
    slippage_model: str = "fixed_pct",
    slippage_params: dict[str, Decimal] | None = None,
) -> tuple[PaperExchange, _FakeHistoricalSource]:
    """Compose a replay-mode PaperExchange with the supplied historical rows."""
    bus = MagicMock()
    bus.subscribe = AsyncMock()
    source = _FakeHistoricalSource(rows)
    paper = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model=slippage_model,  # type: ignore[arg-type]
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test-bot-replay"),
        bus=bus,
        slippage_params=slippage_params or {"fixed_slippage_pct": Decimal("0.0005")},
        pool=_make_pool(),
        mode="replay",
        historical_source=source,  # type: ignore[arg-type]
    )
    return paper, source


def _seed_buy_position(
    paper: PaperExchange,
    *,
    symbol: str = "BTCUSDT",
    qty: Decimal = Decimal("1.0"),
    sl_price: Decimal | None = Decimal("95"),
    tp_price: Decimal | None = Decimal("108"),
    tpsl_mode: str = "Full",
) -> None:
    """Inject an active buy position (mirror test_adapter_fill_semantics pattern)."""
    paper._active_positions[symbol] = {
        "trade_id": 42,
        "side": "buy",
        "qty": qty,
        "entry_price": Decimal("100"),
        "entry_fee": Decimal("0.06"),
        "fees_paid": Decimal("0.06"),
        "sl_price": sl_price,
        "tp_price": tp_price,
        "tp_size": qty,
        "tpsl_mode": tpsl_mode,
    }


def _capture_drain_calls(paper: PaperExchange) -> list[PendingSLTPFill]:
    """Replace ``_drain_sl_tp_fill`` with a capturing no-op (no DB writes).

    Differs from test_adapter_fill_semantics.py ``_install_capture`` which
    calls the original; replay-mode unit tests skip DB persistence
    entirely so the test stays at the algorithmic layer.
    """
    captured: list[PendingSLTPFill] = []

    async def _capture_only(fill: PendingSLTPFill) -> None:
        captured.append(fill)
        # Simulate drain side-effect: position is closed, dict entry deleted.
        paper._active_positions.pop(fill.symbol, None)

    paper._drain_sl_tp_fill = _capture_only  # type: ignore[method-assign]
    return captured


def _make_ohlc_row(
    *,
    symbol: str = "BTCUSDT",
    bucket_start: datetime | None = None,
    open_: Decimal = Decimal("100"),
    high: Decimal = Decimal("110"),
    low: Decimal = Decimal("90"),
    close: Decimal = Decimal("105"),
    source: str = "binance",
) -> OHLCRow:
    return OHLCRow(
        symbol=symbol,
        bucket_start=bucket_start or datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=Decimal("100"),
        source=source,
    )


# --- Constructor + mode validation -----------------------------------------


def test_constructor_replay_mode_requires_historical_source() -> None:
    """Plan §Constructor validation #1."""
    bus = MagicMock()
    with pytest.raises(ValueError, match="historical_source"):
        PaperExchange(
            seed_balance=Decimal("10000.00"),
            slippage_model="fixed_pct",
            fee_rate=Decimal("0.0006"),
            bot_id=BotId("test-bot"),
            bus=bus,
            slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
            pool=_make_pool(),
            mode="replay",
            historical_source=None,
        )


def test_constructor_live_mode_forbids_historical_source() -> None:
    """Plan §Constructor validation #2."""
    bus = MagicMock()
    fake_source = _FakeHistoricalSource([])
    with pytest.raises(ValueError, match="must not pass historical_source"):
        PaperExchange(
            seed_balance=Decimal("10000.00"),
            slippage_model="fixed_pct",
            fee_rate=Decimal("0.0006"),
            bot_id=BotId("test-bot"),
            bus=bus,
            slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
            pool=_make_pool(),
            mode="live",
            historical_source=fake_source,  # type: ignore[arg-type]
        )


def test_constructor_default_mode_is_live() -> None:
    """Plan §0.3 backwards-compat: existing call sites work without mode kwarg.

    Verbatim mirror of services/execution/app/pool.py:198 PaperExchange()
    call shape — no mode/historical_source kwargs.
    """
    bus = MagicMock()
    paper = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test-bot"),
        bus=bus,
        slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
        pool=_make_pool(),
    )
    assert paper._mode == "live"
    assert paper._historical_source is None


# --- start_consuming / run_replay mode-guard -------------------------------


async def test_start_consuming_raises_in_replay_mode() -> None:
    """Plan §start_consuming guard."""
    paper, _ = _make_replay_paper([])
    with pytest.raises(RuntimeError, match="run_replay"):
        await paper.start_consuming()


async def test_run_replay_raises_in_live_mode() -> None:
    """Plan §run_replay guard."""
    bus = MagicMock()
    paper = PaperExchange(
        seed_balance=Decimal("10000.00"),
        slippage_model="fixed_pct",
        fee_rate=Decimal("0.0006"),
        bot_id=BotId("test-bot"),
        bus=bus,
        slippage_params={"fixed_slippage_pct": Decimal("0.0005")},
        pool=_make_pool(),
    )
    with pytest.raises(RuntimeError, match="mode='replay'"):
        await paper.run_replay()


# --- run_replay iteration semantics ----------------------------------------


async def test_run_replay_iterates_historical_source_to_exhaustion() -> None:
    """Plan §run_replay: source consumed to exhaustion; _last_price reflects all candles."""
    rows = [
        _make_ohlc_row(close=Decimal("101")),
        _make_ohlc_row(close=Decimal("102")),
        _make_ohlc_row(close=Decimal("103")),
    ]
    paper, source = _make_replay_paper(rows)
    _capture_drain_calls(paper)  # avoid pollute with no fills (no positions seeded)
    await paper.run_replay()
    assert source.iter_count == 3
    # Last close observed wins in _last_price cache (chronological iteration).
    assert paper._last_price["BTCUSDT"] == Decimal("103")


# --- Intra-candle ordering correctness (BRIEF §12.2:1961-1963 supersession) -


async def test_replay_bullish_candle_fires_tp_before_sl() -> None:
    """Plan §A worked example (counter-example to T-213a Q4-A pessimism).

    Candle (open=100, high=110, low=90, close=105) with sl=95, tp=108.
    T-505 path (100, 110, 90, 105) — bullish, toward-high first.
    Seg 1 (100→110) fires TP at 108 BEFORE Seg 2 (110→90) would have
    crossed SL at 95. Q4-A live mode would have fired SL.
    """
    row = _make_ohlc_row(
        open_=Decimal("100"), high=Decimal("110"), low=Decimal("90"), close=Decimal("105")
    )
    paper, _ = _make_replay_paper([row])
    _seed_buy_position(paper, sl_price=Decimal("95"), tp_price=Decimal("108"))
    captured = _capture_drain_calls(paper)
    await paper.run_replay()
    assert len(captured) == 1
    fill = captured[0]
    assert fill.kind == "tp"
    assert fill.trigger_price == Decimal("108")


async def test_replay_bearish_candle_fires_sl_before_tp() -> None:
    """Plan §B worked example.

    Candle (open=100, high=110, low=90, close=95) with sl=95, tp=108.
    T-505 path (100, 90, 110, 95) — bearish, toward-low first.
    Seg 1 (100→90) fires SL at 95 (low=90 ≤ sl=95 ≤ high=100).
    """
    row = _make_ohlc_row(
        open_=Decimal("100"), high=Decimal("110"), low=Decimal("90"), close=Decimal("95")
    )
    paper, _ = _make_replay_paper([row])
    _seed_buy_position(paper, sl_price=Decimal("95"), tp_price=Decimal("108"))
    captured = _capture_drain_calls(paper)
    await paper.run_replay()
    assert len(captured) == 1
    fill = captured[0]
    assert fill.kind == "sl"
    assert fill.trigger_price == Decimal("95")


async def test_replay_doji_uses_bullish_path() -> None:
    """Plan §C worked example.

    Candle (open=100, high=110, low=90, close=100) — close==open (doji).
    T-505 OQ-2=A: doji uses bullish path → (100, 110, 90, 100).
    With sl=95, tp=108 → TP fires in Seg 1 (toward-high first).
    """
    row = _make_ohlc_row(
        open_=Decimal("100"), high=Decimal("110"), low=Decimal("90"), close=Decimal("100")
    )
    paper, _ = _make_replay_paper([row])
    _seed_buy_position(paper, sl_price=Decimal("95"), tp_price=Decimal("108"))
    captured = _capture_drain_calls(paper)
    await paper.run_replay()
    assert len(captured) == 1
    assert captured[0].kind == "tp"
    assert captured[0].trigger_price == Decimal("108")


# --- OQ-3=A: replay skips hydrate + bus subscribe --------------------------


async def test_replay_skips_paper_positions_hydrate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan OQ-3=A: replay does NOT call _hydrate_active_positions.

    Hydrate is the live-only entry path inside ``start_consuming``;
    ``run_replay`` skips it. Spy on the helper to confirm zero calls.
    """
    paper, _ = _make_replay_paper([_make_ohlc_row()])
    _capture_drain_calls(paper)
    hydrate_spy = AsyncMock()
    monkeypatch.setattr(paper, "_hydrate_active_positions", hydrate_spy)
    await paper.run_replay()
    hydrate_spy.assert_not_called()


async def test_replay_skips_bus_subscribe() -> None:
    """Plan OQ-1=A: replay-mode tick source is direct injection — no NATS subscribe.

    bus.subscribe is the live-only subscription; replay reads from
    historical_source instead. Confirm zero subscribe calls.
    """
    paper, _ = _make_replay_paper([_make_ohlc_row()])
    _capture_drain_calls(paper)
    await paper.run_replay()
    paper._bus.subscribe.assert_not_called()  # type: ignore[attr-defined]


# --- BLOCKER guard: _last_candle cache parity (plan-reviewer concern #6) ---


async def test_replay_market_order_after_segment_processing_finds_last_candle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test #12 BLOCKER guard for plan-reviewer concern #6.

    After one ``_process_replay_candle(ohlc)`` call:
    - ``paper._last_candle[symbol]`` must be populated (no KeyError on read).
    - A subsequent ``place_market_order`` must NOT raise KeyError when
      ``_compute_slippage`` reads ``last_candle.high`` + ``last_candle.low``.
    Composes ``half_spread`` slippage so ``_compute_slippage`` actually
    exercises the cached candle's high/low (not just a constant pct).
    """
    row = _make_ohlc_row(
        open_=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
    )
    paper, _ = _make_replay_paper(
        [row],
        slippage_model="half_spread",
        slippage_params={"half_spread_factor": Decimal("0.001")},
    )
    # Short-circuit DB persistence inside place_market_order so the test
    # focuses on the slippage path (which reads _last_candle).
    persist_spy = AsyncMock()
    monkeypatch.setattr(paper, "_persist_open", persist_spy)
    # Process one candle to populate caches.
    await paper._process_replay_candle(row)
    assert "BTCUSDT" in paper._last_candle
    cached = paper._last_candle["BTCUSDT"]
    assert cached.high == Decimal("110")
    assert cached.low == Decimal("90")
    # Now place a market order — should NOT KeyError on _last_candle.
    result = await paper.place_market_order("BTCUSDT", "buy", Decimal("0.5"))
    assert result is not None
    persist_spy.assert_called_once()
