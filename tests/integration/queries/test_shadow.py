"""Integration tests for :mod:`packages.db.queries.shadow` aggregate JOIN (T-517a1).

Runs against a throwaway PostgreSQL migrated to head. Per L-021 active control:
helper exercising non-trivial SQL (LEFT JOIN with parent_kind discriminator
+ COALESCE on symbol from two parent tables) needs a real-PG round-trip
because mock-only tests can't catch JOIN semantics or COALESCE behavior.

Skipped at collection when ``POSTGRES_TEST_DSN`` is unset.

Mirror :mod:`tests.integration.queries.test_outbox` env-gate + fixture pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from packages.db.queries.shadow import select_shadow_variants_for_aggregate

_T_NOW = datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC)


async def _seed_bot(conn: asyncpg.Connection[asyncpg.Record], bot_id: str) -> None:
    """Insert a minimal bots row needed as FK target for trades / paper_trades."""
    await conn.execute(
        "INSERT INTO bots "
        "(bot_id, display_name, created_at, status, exchange_mode, "
        " config_hash, config_applied_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        bot_id,
        f"{bot_id} display",
        _T_NOW,
        "active",
        "paper",
        "sha256:smoke",
        _T_NOW,
    )


async def _seed_live_trade(
    conn: asyncpg.Connection[asyncpg.Record],
    *,
    bot_id: str,
    symbol: str,
) -> int:
    """Insert minimal live trade (orders + trades). Returns trade id."""
    open_order_id = await conn.fetchval(
        """
        INSERT INTO orders
            (bot_id, signal_id, correlation_id, exchange_order_id, exchange,
             symbol, side, order_type, qty, price, status, requested_at,
             filled_at, idempotent)
        VALUES ($1, NULL, $2, $3, 'bybit', $4, 'buy', 'market',
                $5, $6, 'filled', $7, $7, false)
        RETURNING id
        """,
        bot_id,
        f"cid-{bot_id}-{symbol}",
        f"ord-{bot_id}-{symbol}",
        symbol,
        Decimal("1"),
        Decimal("100"),
        _T_NOW,
    )
    trade_id = await conn.fetchval(
        """
        INSERT INTO trades
            (bot_id, signal_id, open_order_id, symbol, side, entry_price,
             qty, notional_usd, status, opened_at)
        VALUES ($1, NULL, $2, $3, 'buy', $4, $5, $6, 'open', $7)
        RETURNING id
        """,
        bot_id,
        open_order_id,
        symbol,
        Decimal("100"),
        Decimal("1"),
        Decimal("100"),
        _T_NOW,
    )
    return int(trade_id)


async def _seed_paper_trade(
    conn: asyncpg.Connection[asyncpg.Record],
    *,
    bot_id: str,
    symbol: str,
) -> int:
    """Insert minimal paper trade (paper_orders + paper_trades). Returns trade id."""
    open_order_id = await conn.fetchval(
        """
        INSERT INTO paper_orders
            (bot_id, signal_id, correlation_id, exchange_order_id, exchange,
             symbol, side, order_type, qty, price, status, requested_at,
             filled_at, idempotent)
        VALUES ($1, NULL, $2, $3, 'paper', $4, 'buy', 'market', $5, $6,
                'filled', $7, $7, false)
        RETURNING id
        """,
        bot_id,
        f"papercid-{bot_id}-{symbol}",
        f"paperord-{bot_id}-{symbol}",
        symbol,
        Decimal("1"),
        Decimal("100"),
        _T_NOW,
    )
    paper_trade_id = await conn.fetchval(
        """
        INSERT INTO paper_trades
            (bot_id, signal_id, open_order_id, symbol, side, entry_price,
             qty, notional_usd, status, opened_at)
        VALUES ($1, NULL, $2, $3, 'buy', $4, $5, $6, 'open', $7)
        RETURNING id
        """,
        bot_id,
        open_order_id,
        symbol,
        Decimal("100"),
        Decimal("1"),
        Decimal("100"),
        _T_NOW,
    )
    return int(paper_trade_id)


async def _insert_terminated_variant(
    conn: asyncpg.Connection[asyncpg.Record],
    *,
    parent_trade_id: int,
    parent_kind: str,
    bot_id: str,
    variant_name: str,
    realized_pnl: Decimal,
    mfe_pct: float | None,
    mae_pct: float | None,
    created_at: datetime,
) -> None:
    """Insert a fully-terminated shadow_variant row (charter: terminated_at + realized_pnl)."""
    await conn.execute(
        """
        INSERT INTO shadow_variants
            (parent_trade_id, bot_id, variant_name, side, entry_price, qty,
             created_at, terminated_at, terminal_outcome, realized_pnl,
             mfe_pct, mae_pct, parent_kind)
        VALUES ($1, $2, $3, 'buy', $4, $5, $6, $7, 'tp_full', $8, $9, $10, $11)
        """,
        parent_trade_id,
        bot_id,
        variant_name,
        Decimal("100"),
        Decimal("1"),
        created_at,
        created_at + timedelta(minutes=30),
        realized_pnl,
        mfe_pct,
        mae_pct,
        parent_kind,
    )


@pytest.mark.asyncio
async def test_select_shadow_variants_for_aggregate_join_live_parent(
    migrated_db_dsn: str,
) -> None:
    """JOIN trades for parent_kind='live'; COALESCE picks parent_symbol from trades."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await _seed_bot(conn, "alpha")
        live_trade_id = await _seed_live_trade(conn, bot_id="alpha", symbol="BTCUSDT")
        await _insert_terminated_variant(
            conn,
            parent_trade_id=live_trade_id,
            parent_kind="live",
            bot_id="alpha",
            variant_name="conservative",
            realized_pnl=Decimal("12.50"),
            mfe_pct=0.025,
            mae_pct=-0.005,
            created_at=_T_NOW,
        )
        await _insert_terminated_variant(
            conn,
            parent_trade_id=live_trade_id,
            parent_kind="live",
            bot_id="alpha",
            variant_name="aggressive",
            realized_pnl=Decimal("-5.25"),
            mfe_pct=0.040,
            mae_pct=-0.030,
            created_at=_T_NOW,
        )
        rows = await select_shadow_variants_for_aggregate(
            conn,
            symbol="BTCUSDT",
            bot_id=None,
            from_at=None,
            to_at=None,
        )
        assert len(rows) == 2
        for r in rows:
            assert r.parent_symbol == "BTCUSDT"
            assert r.parent_kind == "live"
            assert r.bot_id == "alpha"
        names = {r.variant_name for r in rows}
        assert names == {"conservative", "aggressive"}
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_select_shadow_variants_for_aggregate_join_paper_parent(
    migrated_db_dsn: str,
) -> None:
    """JOIN paper_trades for parent_kind='paper'; COALESCE picks symbol from paper_trades."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await _seed_bot(conn, "alpha")
        paper_id = await _seed_paper_trade(conn, bot_id="alpha", symbol="ETHUSDT")
        await _insert_terminated_variant(
            conn,
            parent_trade_id=paper_id,
            parent_kind="paper",
            bot_id="alpha",
            variant_name="conservative",
            realized_pnl=Decimal("8.00"),
            mfe_pct=0.018,
            mae_pct=-0.004,
            created_at=_T_NOW,
        )
        await _insert_terminated_variant(
            conn,
            parent_trade_id=paper_id,
            parent_kind="paper",
            bot_id="alpha",
            variant_name="aggressive",
            realized_pnl=Decimal("15.00"),
            mfe_pct=0.050,
            mae_pct=-0.020,
            created_at=_T_NOW,
        )
        rows = await select_shadow_variants_for_aggregate(
            conn,
            symbol="ETHUSDT",
            bot_id=None,
            from_at=None,
            to_at=None,
        )
        assert len(rows) == 2
        for r in rows:
            assert r.parent_symbol == "ETHUSDT"
            assert r.parent_kind == "paper"
        # Charter: only terminated + realized_pnl IS NOT NULL rows.
        for r in rows:
            assert r.realized_pnl is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_select_shadow_variants_for_aggregate_mixed_parents_and_filters(
    migrated_db_dsn: str,
) -> None:
    """Live + paper trades for same symbol, multiple bots; filter narrows correctly."""
    conn = await asyncpg.connect(dsn=migrated_db_dsn)
    try:
        await _seed_bot(conn, "alpha")
        await _seed_bot(conn, "beta")
        # Live trade for alpha (BTCUSDT) — variant inside window.
        live_alpha = await _seed_live_trade(conn, bot_id="alpha", symbol="BTCUSDT")
        await _insert_terminated_variant(
            conn,
            parent_trade_id=live_alpha,
            parent_kind="live",
            bot_id="alpha",
            variant_name="conservative",
            realized_pnl=Decimal("10"),
            mfe_pct=0.020,
            mae_pct=-0.005,
            created_at=_T_NOW,
        )
        # Paper trade for beta (BTCUSDT) — variant inside window.
        paper_beta = await _seed_paper_trade(conn, bot_id="beta", symbol="BTCUSDT")
        await _insert_terminated_variant(
            conn,
            parent_trade_id=paper_beta,
            parent_kind="paper",
            bot_id="beta",
            variant_name="aggressive",
            realized_pnl=Decimal("20"),
            mfe_pct=0.040,
            mae_pct=-0.025,
            created_at=_T_NOW,
        )
        # Variant for unrelated ETHUSDT — must NOT appear in BTCUSDT aggregate.
        live_alpha_eth = await _seed_live_trade(conn, bot_id="alpha", symbol="ETHUSDT")
        await _insert_terminated_variant(
            conn,
            parent_trade_id=live_alpha_eth,
            parent_kind="live",
            bot_id="alpha",
            variant_name="conservative",
            realized_pnl=Decimal("99"),
            mfe_pct=0.090,
            mae_pct=-0.001,
            created_at=_T_NOW,
        )
        # Variant outside window (created 31 days ago) — must be filtered out.
        live_alpha_old = await _seed_live_trade(conn, bot_id="alpha", symbol="BTCUSDT")
        await _insert_terminated_variant(
            conn,
            parent_trade_id=live_alpha_old,
            parent_kind="live",
            bot_id="alpha",
            variant_name="aggressive",
            realized_pnl=Decimal("-1"),
            mfe_pct=0.001,
            mae_pct=-0.001,
            created_at=_T_NOW - timedelta(days=31),
        )

        # 1) symbol-only filter → both BTCUSDT variants (alpha live + beta paper);
        #    excludes ETHUSDT; includes the 31-day-old one (no window filter).
        rows_all = await select_shadow_variants_for_aggregate(
            conn,
            symbol="BTCUSDT",
            bot_id=None,
            from_at=None,
            to_at=None,
        )
        assert len(rows_all) == 3
        symbols = {r.parent_symbol for r in rows_all}
        assert symbols == {"BTCUSDT"}

        # 2) bot_id="beta" filter → only paper variant.
        rows_beta = await select_shadow_variants_for_aggregate(
            conn,
            symbol="BTCUSDT",
            bot_id="beta",
            from_at=None,
            to_at=None,
        )
        assert len(rows_beta) == 1
        assert rows_beta[0].bot_id == "beta"
        assert rows_beta[0].parent_kind == "paper"

        # 3) Window filter (last 7 days) → excludes the 31-day-old row.
        rows_window = await select_shadow_variants_for_aggregate(
            conn,
            symbol="BTCUSDT",
            bot_id=None,
            from_at=_T_NOW - timedelta(days=7),
            to_at=_T_NOW + timedelta(days=1),
        )
        assert len(rows_window) == 2
        # The 31-day-old "aggressive" variant on alpha is excluded; only the 2 in-window.
    finally:
        await conn.close()
