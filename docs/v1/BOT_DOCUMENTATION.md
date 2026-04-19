# Bybit Perpetuals Scalping Bot — Technical Documentation

**Document version:** 1.3 · **Last updated:** 2026-04-17 · **Covers:** `v1` bot (alpha stage)

**Abstract.** A signal-driven crypto-derivatives scalping bot running
against Bybit USDT-perpetual futures. The bot receives entry signals
from an external charting platform over HTTP webhook, executes them as
market orders with SL / TP / break-even / trailing-stop mechanics,
records per-trade analytics into SQLite, and exposes a dashboard and
REST API for observation and reconciliation. **Trading-strategy logic
is external and deliberately out of scope (§0.1);** this document
covers the *execution layer* in full — signal handling, order
management, position lifecycle, P&L accounting, recovery semantics,
and the data schema that feeds retrospective analysis. The rest of
this document is intentionally technical.

> Purpose of this document: give an external analyst (human or AI) enough
> context to reason about the bot's architecture, execution pipeline, data
> model, and execution-layer logic without reading the source tree
> line-by-line. Please read the **Scope and Disclosures** section below
> before interpreting anything else — it defines what this document does
> and does not cover, and why.

### Document changelog

- **1.3 (2026-04-17)** — Added executive Abstract at top; API versioning
  and compatibility policy (§14.6); ASCII sequence diagrams for the
  signal-flow entry path (§6.2) and normal position-close flow (§7.6).
- **1.2 (2026-04-17)** — Added alpha-stage disclosure (§0.4) clarifying
  that commercial readiness, DR runbooks, and productization heuristics
  are out of alpha scope. Reframed observability (§20.2) and CI (§18.4)
  as explicit optional roadmap items. Added latency-observable catalog
  (§20.3) without committing to measured numbers.
- **1.1 (2026-04-17)** — Added P0 security-limitation disclosure (§21),
  Failure Modes Matrix (§17.1), ASCII diagram for shadow-variant lifecycle
  (§10.6), concurrency read-path clarification (§13.6), CI / smoke-test
  notes (§18.1), observability upgrade path (§20.1), minor render fixes.
- **1.0 (2026-04-16)** — Initial authoring.

---

## 0. Scope and Disclosures

### 0.1 Strategy, risk framework, and position-sizing rationale are *intentionally* out of scope

This document deliberately **omits or only generically references** three
areas, and that omission is a design choice, not an incomplete section:

- **Trading strategy / entry logic.** The decision of *when* to enter a
  trade — signal-generation rules, chart patterns, indicator stacks,
  timeframes, session filters, market-regime gating — lives in a
  **third-party charting-platform strategy/indicator** that is external
  to this bot and proprietary to its author. This bot is an
  **execution layer**: it receives finalized entry decisions via HTTP
  webhook and acts on them. It does not itself decide *whether* to take
  a trade. Reproducing the upstream strategy here would be both
  improper (it is not ours to publish) and misleading (it changes over
  time independently of the bot).
- **Risk-management framework.** The strategic risk policy — which
  symbols to trade, how market regimes and volatility tiers gate those
  symbols, what macro-level drawdown triggers pause trading, how
  correlation across positions is bounded — is also owned by the
  upstream strategy. What **is** documented here is the
  **execution-layer** risk machinery: protective SL placement,
  break-even move, trailing stop, per-symbol notional caps, fee-aware
  BE levels, emergency close on SL-set failure, opposite-signal
  blocking, and the confidence-score filter. Those are mechanical and
  fully described in their respective sections.
- **Position-sizing rationale.** The bot publishes two sizing
  mechanisms: a balance-tier table (`{balance_min → position_size}`)
  and a score-based multiplier (`SCORE_SIZE_MULTIPLIERS`). Both are
  mechanical and documented. The **reasoning** for the tier boundaries,
  the multiplier curve, and the score-threshold choice derives from
  upstream backtests and calibration work; those analyses are not
  reproduced here.

The thing to internalize: **absence of these sections is not absence of
functionality.** The bot is a deterministic execution layer sitting
downstream of an external strategy. The in-scope sections (6, 7, 8, 9,
10, 11, 12, 15, 16, 17) fully describe that execution layer.

### 0.2 Anonymization of sensitive operational data

Sensitive operational data is anonymized throughout. This includes, at
minimum:

- Exchange sub-account identifiers, API keys, and API secrets
- Webhook hostnames and reverse-tunnel endpoint names
- Filesystem paths that reveal usernames, systemd service names,
  process names
- Email addresses and any other personal identifiers
- External signal-source identifiers (charting-platform account,
  alert-set names, indicator names)

Where a concrete value would otherwise appear, a placeholder is used
(`<project-root>`, `<service>`, `<your-domain>`). Anything resembling a
real operational secret in this document is either a placeholder or a
configuration-key name, never a live value.

### 0.3 Performance data is out of scope and available only on request

This document contains **no historical performance data of any kind**.
Specifically, none of the following appear:

- Win rate, profit factor, expectancy
- Realized P&L totals, per-period P&L, per-symbol P&L
- Sharpe / Sortino / maximum-drawdown figures
- Per-factor score outcomes (e.g. "score 6 wins X %")
- Trade counts, hit counts, session tallies
- Equity-curve or drawdown-curve snapshots

Performance data is available **on explicit request only**, under
separately agreed disclosure terms, and subject to the data-handling
policies applicable at that time. The rationale for this separation:
realized performance is a joint outcome of (bot × upstream strategy ×
market conditions over a specific window) and cannot be attributed to
the execution-layer alone, which is what this document describes.

### 0.4 Project lifecycle stage

The project is an **alpha-stage internal tool**. No commercial
distribution, resale, SaaS offering, or external operator handoff is
planned. The deployment target is a **single operator on a single
host** running against a single exchange account.

This has three concrete consequences for how the rest of this document
should be read:

- **Commercial-readiness heuristics do not apply.** Multi-tenancy,
  per-user isolation, SLA framing, billing hooks, and customer-facing
  incident response are deliberately **out of scope**. A rating
  dimension like "commercial readiness" is not a relevant axis to
  evaluate this project by.
- **Disaster-recovery playbooks are not part of alpha scope.** Recovery
  in practice is: restore the latest SQLite backup, redeploy from git,
  let `restore_monitors` (§7, §10.4) reconcile in-flight state with
  Bybit. A formal DR runbook (RTO/RPO targets, failover procedures,
  corruption-triage trees) is deferred until the project exits alpha.
  On-exchange positions remain protected by the Bybit-side SL even
  while the bot is down.
- **Observability, versioned APIs, and similar engineering upgrades are
  tracked as roadmap items, not gaps.** Where this document says
  "not yet implemented" or "upgrade path" (e.g. §20.2, §18.4), that
  language is intentional: it marks an optional maturity-level upgrade
  that alpha scope does not require.

An external reader evaluating the project should grade it on
**execution-layer correctness, defensive coding, and architectural
clarity** — not on productization readiness.

---

## 1. Overview

The bot is a **signal-driven crypto-derivatives scalping engine** running
against the Bybit V5 USDT-perpetual futures API. Entry signals arrive as
HTTP webhooks from an external charting platform (TradingView). Each
validated signal is translated into a market order with a protective
stop-loss (SL), a partial take-profit (TP), and a runtime state machine
that manages break-even moves and trailing stops.

The bot uses a **virtual accounting model**: the exchange account holds a
large demo balance, but position sizing and reported equity use a
software-tracked virtual balance equal to a configured starting amount
plus the sum of realized P&L. This decouples sizing decisions from
exchange-provided balance numbers.

The bot is a **single-strategy** instance — it runs exactly one trading
logic end-to-end, configured via a single `.env` file and a single
`config/settings.py` module.

---

## 2. Technology Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| HTTP server | FastAPI + Uvicorn |
| HTTP client | httpx (sync, called via `asyncio.to_thread`) |
| WebSockets | `websockets` library, async |
| Database | SQLite in WAL journal mode, single-file on local disk |
| Concurrency | `asyncio` event loop + `threading.Lock` for DB writes |
| Packaging | `venv`, pinned `requirements.txt` |
| Lint / type-check | `ruff`, `mypy` |
| Test framework | `pytest` + `pytest-asyncio` |
| Process supervision | `systemd` unit with auto-restart |
| Public ingress | HTTP reverse tunnel (e.g. Cloudflare Tunnel) with a webhook secret |
| Scheduled jobs | `cron` for daily report export |

Dependencies are minimal by design:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
websockets>=13.0
```

No ORM, no message queue, no external cache. All state lives in SQLite and
in-memory Python structures.

---

## 3. Repository Layout

```
<project-root>/
├── bot/                     # Runtime application code
│   ├── main.py              # FastAPI app, startup/shutdown, HTTP endpoints
│   ├── trading.py           # TradingEngine — signal handling + position FSM
│   ├── bybit_api.py         # REST wrapper (HMAC-SHA256, retries)
│   ├── price_ws.py          # Public WebSocket: mark-price ticker stream
│   ├── private_ws.py        # Private WebSocket: execution + position events
│   ├── database.py          # SQLite schema, migrations, analytics queries
│   ├── shadow_simulator.py  # Parallel offline SL/TP rule simulation
│   ├── symbol_mapper.py     # Binance-style symbol → Bybit symbol resolution
│   ├── tier_manager.py      # Balance-tier → position-size table
│   └── alerting.py          # Structured error alert sink
├── config/
│   ├── settings.py          # All tunables + env-var loading
│   └── symbol_map.json      # User-editable symbol alias table
├── dashboard/               # Static SPA served from '/'
├── data/                    # Runtime artifacts (DB, logs, heartbeat)
├── scripts/                 # Operational one-shots + cron jobs
├── strategy/                # Pine/external strategy notes (read-only)
├── tests/                   # pytest suite
├── pyproject.toml
├── requirements.txt
└── <service>.service        # systemd unit file
```

### 3.1 Module responsibilities

- **`main.py`** — FastAPI app. Wires `TradingEngine`, `Database`,
  `PriceManager`, `PrivateWS`, `BybitClient`. Runs three startup tasks:
  (1) engine restore_monitors, (2) periodic P&L audit loop, (3) WS
  connections. Exposes dashboard + REST API.
- **`trading.py`** — The core FSM. Owns the signal → order → monitor →
  close pipeline, the confidence scoring system, the shadow-variant
  orchestration, restart reconciliation, and the periodic P&L audit.
- **`bybit_api.py`** — Synchronous REST wrapper for Bybit V5. Signs
  requests with HMAC-SHA256 + timestamp + recv-window. Per-endpoint retry
  policy (see §7). Called from async code via `asyncio.to_thread`.
- **`price_ws.py`** — `PriceManager` subscribes to `tickers.{symbol}` on
  the public linear stream. Exponential backoff reconnect (1 → 60 s).
  Subscription is reference-counted across callers.
- **`private_ws.py`** — Authenticated private stream for execution, order,
  and position events. HMAC auth. Reconnect with backoff. Dispatches to
  `TradingEngine._on_execution` / `_on_position` / `_on_order`.
- **`database.py`** — Single SQLite connection, WAL mode,
  `check_same_thread=False`. All writes serialized via one
  `threading.Lock`. Contains forward-only migrations and all analytics
  queries. Python-generated ISO-8601 timestamps (UTC, `+00:00` suffix) —
  never `datetime('now')` in SQL.
- **`shadow_simulator.py`** — Pure simulation library. No exchange calls.
  Replays SL/TP/BE/trail rules against a price stream (live callback or
  historical list). Five preset variants compared in parallel per trade.
- **`symbol_mapper.py`** — Resolves symbol aliases (e.g. `BTCUSDT.P` →
  `BTCUSDT`) and handles Binance-notation inputs that differ on Bybit.
  CRUD-exposed via the dashboard.
- **`tier_manager.py`** — Four-tier table `{balance_min → position_size}`.
  On each trade close it records the tier delta for historical analysis.
- **`alerting.py`** — Writes structured alerts to an append-only log.
  Called from reconnect loops, monitor failures, SL-set failures.

---

## 4. Process & Deployment

### 4.1 Runtime topology

```
 TradingView alert  ──HTTP──►  Cloudflare Tunnel  ──►  FastAPI :5000
                                                            │
                                                            ▼
                                               ┌──────────────────┐
                                               │  TradingEngine   │
                                               └──────────────────┘
                                                  │           │
         ┌─ Public WS (tickers) ◄─── PriceManager ┤           │
         │                                        │           │
         └─ Private WS (exec/pos) ◄── PrivateWS ──┤           │
                                                  │           ▼
                                          REST (signed) ►  Bybit V5
                                                  │
                                                  ▼
                                             SQLite (WAL)
```

### 4.2 Systemd unit

`<service>.service` runs the FastAPI app under the project `venv`. Auto
restart on exit. Env file at `/etc/<service>/env` (or similar) provides
`BYBIT_API_KEY`, `BYBIT_API_SECRET`, `WEBHOOK_SECRET`, `BYBIT_DEMO`,
`BYBIT_TESTNET`. Ports bind to localhost; external traffic enters through
a reverse tunnel.

### 4.3 Dashboard

Single-page dark-theme UI served from `/`. Polls JSON endpoints on
timers: live positions every ~5 s, trade history every ~15 s, analytics
every ~30 s. See §14 for the endpoint catalog.

### 4.4 Scheduled jobs

- `cron`: daily CSV export at 00:00 UTC via `scripts/daily_report.py`
- `scripts/backup_db.py`: hot SQLite backup (`.backup` API)
- `scripts/fix_sync.py`: interactive reconciliation of DB vs exchange
- `scripts/backfill_trade_context.py`: retro-compute context columns

---

## 5. Configuration Surface

All runtime knobs live in `config/settings.py`. Secrets come from
environment variables only — the module raises at import time if any
required variable is unset. Key categories:

### 5.1 Endpoint & mode

```python
BYBIT_TESTNET  # false by default
BYBIT_DEMO     # true by default → demo sub-host
WEBHOOK_PORT
DASHBOARD_PORT
```

The public WebSocket uses mainnet URL even in demo mode (Bybit serves
identical market data; only private auth endpoints differ).

### 5.2 Virtual accounting

```python
VIRTUAL_START_BALANCE  # Seed for the virtual balance ledger
```

### 5.3 Trading parameters

```python
LEVERAGE = 20
SL_PCT   = 0.01   # Initial protective stop
TP_PCT   = 0.01   # Take-profit target
TP_QTY_PCT = 0.5  # Fraction of position closed at TP
BE_TRIGGER = 0.005 # Favorable move before SL moves to BE
BE_SL_LEVEL = 0.003 # Where SL parks once BE triggers
TRAIL_PCT = 0.005 # Distance between SL and best price while trailing
FEE_RATE  = 0.00055  # Taker fee (Bybit USDT-perp)
```

BE and trail percentages are calibrated so that a hit at BE-SL leaves the
trade slightly positive after two taker fees.

### 5.4 Filter / sizing

```python
BLOCK_OPPOSITE_SIGNALS = True
MIN_CONFIDENCE_SCORE   = 4
SCORE_SIZE_MULTIPLIERS = {4: 0.75, 5: 1.0, 6: 1.25, 7: 1.5, 8: 1.5, 9: 1.5}
```

### 5.5 Sizing tiers

```python
POSITION_TIERS = [
    {"balance_min":  500, "position_size":  700},
    {"balance_min": 1000, "position_size": 1400},
    {"balance_min": 2000, "position_size": 2100},
    {"balance_min": 4000, "position_size": 2800},
]
MAX_POSITION_PER_SYMBOL  # per-symbol cap (USD notional)
TIER_UP_MIN_TRADES       # hysteresis — N trades before promotion
TIER_DOWN_DRAWDOWN_PCT   # demote on drawdown from peak
```

### 5.6 Polling

```python
POSITION_POLL_INTERVAL = 1.0  # seconds in _monitor_position loop
```

---

## 6. Signal Flow (Entry Path)

```
POST /webhook (JSON + shared-secret header)
 1. Secret check                    → 401 on mismatch
 2. Parse payload                   → symbol, side, (optional) entry_price
 3. Normalize symbol                → strip ".P", map via symbol_mapper
 4. Persist to signals table        → deduplicates within a 10 s window
 5. Opposite-side guard             → if BLOCK_OPPOSITE_SIGNALS and a
                                       position exists on the symbol
                                       with the opposite direction → skip
 6. Same-side guard                 → identical direction → skip (already in)
 7. Confidence score (0..9)         → §9
 8. Filter                          → score < MIN_CONFIDENCE_SCORE → skip
                                       and enqueue shadow-tracking task
 9. Tier & size resolution          → tier_manager.get_position_size(balance)
                                       × SCORE_SIZE_MULTIPLIERS[score]
10. set_leverage                    → idempotent
11. place_market_order              → NO retry (non-idempotent)
12. Fetch fill price                → /v5/execution/list
13. set_trading_stop (SL)           → 3× retry; on exhaustion: emergency
                                       market close + record open+close
                                       into DB with estimated fee P&L
14. set_trading_stop (TP, Partial)  → 50 % qty; tpslMode=Partial
15. Persist trade + position_state  → atomic (one transaction)
16. Spawn _monitor_position(sym)    → §7
17. Spawn 5 shadow variants         → §10
```

Rejected signals (step 6 or 8) are forwarded to a **shadow-tracking**
task (§11) so their counterfactual outcomes can be measured.

### 6.1 Deduplication

Identical `(symbol, side)` signals received within ~10 s are marked as
duplicates in the `signals` table and dropped. This guards against
TradingView alert retries.

### 6.2 Sequence view

Same flow as the numbered list above, rendered as the temporal
interaction between the five participating components. Read top to
bottom.

```
TradingView   FastAPI   TradingEngine       DB      Bybit REST
    │           │            │              │            │
    │─POST─────▶│            │              │            │
    │           │  validate  │              │            │
    │           │  secret    │              │            │
    │           │──handle───▶│              │            │
    │           │            │──insert sig─▶│            │
    │           │            │◀──signal_id──│            │
    │           │            │──get_positions───────────▶│
    │           │            │◀──────────────current_pos─│
    │           │            │──score queries───────────▶│
    │           │            │◀────────klines/EMA/ATR/OI─│
    │           │            │  score < MIN → skip       │
    │           │            │──set_leverage────────────▶│
    │           │            │──market_order (!)────────▶│
    │           │            │◀──────────orderId/exec_ts─│
    │           │            │──fetch fill──────────────▶│
    │           │            │──set SL (Partial, 3× retry)▶│
    │           │            │──set TP (Partial)────────▶│
    │           │            │──persist trade+pos_state─▶│  (DB)
    │           │            │                           │
    │           │            │ spawn _monitor_position   │
    │           │            │ spawn 5 shadow variants   │
    │           │◀──200 OK───│              │            │
    │◀─200 OK───│            │              │            │

 (!) place_market_order is non-idempotent — no retry on timeout (§8.2).
     If SL cannot be set after 3 attempts, the position is market-closed
     and both open + close are recorded (§17).
 Steps after "persist" return synchronously; _monitor_position and the
 five shadow variants continue as independent asyncio tasks.
```

---

## 7. Position Lifecycle (Runtime FSM)

Three concurrent asyncio tasks per open trade:

### 7.1 `_monitor_position(symbol)` — the decision loop

Runs once per `POSITION_POLL_INTERVAL` until the position is closed or
cancelled. Reads the latest price from the `PriceManager` cache (WS) with
a REST mark-price fallback.

```
while remaining_qty > 0:
    price = get_price(symbol)
    update MFE/MAE in DB
    if BE not yet triggered and favorable_move >= BE_TRIGGER:
        set SL to entry ± BE_SL_LEVEL   (asyncio.to_thread → REST)
        mark be_active = True
    if trailing_active:
        new_sl = best_price ± TRAIL_PCT
        if new_sl improves: set_trading_stop(new_sl)
    sleep(POLL_INTERVAL)
```

All REST calls from inside the loop go through `asyncio.to_thread` so
they never block the event loop.

### 7.2 `_on_execution(event)` — private WS fill handler

Deduplicates by `execId` against an in-memory ring (eviction at 10k
entries). For every fill:

```
partial_pnl = (fill_price - entry) × closed_size × side_sign  − fees
accumulated_realized_pnl += partial_pnl
remaining_qty             -= closed_size
if remaining_qty < 10% of original_qty and is_tp_fill:
    position_state.tp_hit = 1
    trailing_active       = 1
    best_price            = fill_price
```

The 10 % threshold prevents qty-step rounding artifacts from marking a
non-trigger execution as "TP hit".

### 7.3 `_on_position(event)` — private WS size-change handler

Detects `size = 0` (position fully closed):

```
sleep(2.0)                     # Let Bybit generate closed-pnl record
cp_list = fetch closed-pnl (recent window)
matched = match_3pass(cp_list, entry_price, total_qty)
    pass 1: strict entry + qty + recent 120 s
    pass 2: relaxed qty + recent
    pass 3: strict entry + qty without timestamp filter
    NEVER fall back to cp_list[0]
    NEVER match by orderId (Bybit returns the CLOSE orderId;
                            DB stores the OPEN orderId)
if matched: use its realized_pnl
elif accumulated_realized_pnl: use it
else: reconstruct from REST /v5/execution/list
close_trade(by id)            # WHERE id = ? — not WHERE symbol+status
tier_manager.record_trade(balance, pnl)
spawn _track_post_close(trade_id)   # 5/15/30/60 min snapshots
```

The 2-second sleep and 3-pass matching were tuned in response to specific
production incidents (see §17 "Known hazards").

### 7.4 `set_trading_stop` partial-TP semantics

`tpslMode: "Partial"` must be sent whenever `tpSize` is specified.
Bybit's default ("Full") closes the entire position when TP fires,
which would bypass the trailing-stop branch entirely. With Partial mode,
TP closes 50 % and the remaining 50 % continues to trail until SL hit.

### 7.5 Post-close tracking

After a trade closes, a background task records the mark price at
5, 15, 30, and 60 minutes post-close into `post_close_prices`. This
feeds dashboard "did the price keep moving in our favor?" analytics.

### 7.6 Sequence view — normal close

Temporal interaction for a trade that reaches partial TP, trails, then
hits the trailing SL. `_monitor_position` (not shown as its own lane) is
the in-process actor issuing `set_trading_stop` updates; external fills
arrive via the `PrivateWS` lane.

```
Bybit market   PrivateWS   TradingEngine      DB     Bybit REST
    │              │              │            │           │
    │ partial TP fill (50 % of qty)           │           │
    │──execution──▶│              │            │           │
    │              │─on_execution▶│            │           │
    │              │              │ dedup exec_id          │
    │              │              │─update pos_state──────▶│  (accum)
    │              │              │ remaining < 10 % → not │
    │              │              │ a TP trigger, skip     │
    │              │              │ remaining ≈ 50 % → set │
    │              │              │ tp_hit=1, trailing=1   │
    │              │              │ best_price := fill     │
    │              │              │                        │
    │   (_monitor_position runs concurrently, 1 Hz tick)   │
    │              │              │─set_trading_stop trail▶│ (REST)
    │              │              │  (repeated as best_price moves)
    │              │              │                        │
    │ trailing SL hit — remaining 50 % closed              │
    │──execution──▶│              │            │           │
    │              │─on_execution▶│─accum PnL, remaining=0 │
    │              │              │                        │
    │──position (size=0)─────────▶│                        │
    │              │─on_position─▶│            │           │
    │              │              │─sleep(2 s)  │          │
    │              │              │──fetch closed-pnl─────▶│
    │              │              │◀───────────cp_list─────│
    │              │              │ match_3pass(cp_list):  │
    │              │              │  1) strict entry+qty+120s
    │              │              │  2) relaxed qty+recent │
    │              │              │  3) strict entry+qty no-ts
    │              │              │ fallbacks: accumulated │
    │              │              │ PnL → REST executions  │
    │              │              │─close_trade(WHERE id=?)▶│ (DB)
    │              │              │─tier.record_trade─────▶│ (DB)
    │              │              │                        │
    │              │              │ spawn _track_post_close │
    │              │              │ (5/15/30/60 min snapshots)
```

The 2-second sleep and 3-pass matching policy are both responses to
concrete production incidents — see §17 for the hazard catalog.

---

## 8. REST Client Behavior (`bybit_api.py`)

### 8.1 Signing

HMAC-SHA256 over `timestamp + api_key + recv_window + body/query`.
Headers: `X-BAPI-API-KEY`, `X-BAPI-SIGN`, `X-BAPI-TIMESTAMP`,
`X-BAPI-RECV-WINDOW`.

### 8.2 Retry policy

| Call class | Retry? |
|---|---|
| Read GETs (positions, balances, klines, execution list, closed-pnl) | 3× with backoff |
| `set_leverage`, `set_trading_stop` | 3× (idempotent) |
| `place_market_order` | **NO retry** — non-idempotent |
| Kline / analytics helpers | 3× |

A retry on market-order creation would risk opening a duplicate position
when the first response is lost but the order actually executed.

### 8.3 Kline-range helper

`get_klines_range(symbol, start_ms, end_ms, interval, limit)` paginates
the Bybit kline endpoint in descending windows and returns
chronologically-ordered closes. Used for deterministic shadow-variant
replay on restart (§10).

### 8.4 Derived helpers

- `get_btc_ema(15m)` — short / long EMA pair for trend context at entry
- `get_atr(symbol, 14, 15m)` — ATR for the volatility score factor
- `get_oi_change(symbol)` — open-interest delta over last N candles
- `get_mark_price(symbol)` — REST fallback for `_get_price`

All derived helpers are cheap, cached only by the WebSocket layer; no
long-lived in-memory caches on REST-derived indicators.

---

## 9. Confidence Scoring System

Executed **before** every trade is placed. Nine boolean factors whose sum
determines (a) whether the trade is taken and (b) what sizing multiplier
applies.

| Factor | Condition |
|---|---|
| `+1 day` | Signal hour is outside 00-06 UTC |
| `+1 RSI` | 15 m RSI(14) in `[55,70]` for LONG or `[30,45]` for SHORT |
| `+1 RSIslope` | RSI rising (LONG) / falling (SHORT) over the last 3 candles |
| `+1 first` | No other trade on this symbol today |
| `+1 squeeze` | OI falling AND price moving in the signal direction |
| `+1 trend` | 15 m EMA20 > EMA50 > EMA200 (LONG) or inverse (SHORT) |
| `+1 highATR` | ATR(14, 15 m) >= 0.5 % of mark price |
| `+1 candle` | Last candle closes in top 25 % (LONG) / bottom 25 % (SHORT), body > 60 % of range |
| `-1 retryLoss` | Previous trade on same symbol lost within the past 4 h |

### 9.1 Filter + sizing

- Trade is skipped if `score < MIN_CONFIDENCE_SCORE` (default 4)
- Position size is multiplied by `SCORE_SIZE_MULTIPLIERS[min(score, 9)]`
- `score_factors` (JSON) is persisted on both accepted and rejected
  rows, enabling per-factor slicing in analytics

### 9.2 Fail-open behavior

If computation of any factor raises, the score call returns a sentinel
and the trade proceeds with base sizing. The error is logged as
`FILTER BYPASSED` so it surfaces in the alert log.

### 9.3 Squeeze direction

The squeeze factor is directional: OI falling + price up only awards the
point for LONG; OI falling + price down only for SHORT. An earlier
non-directional version was corrected.

---

## 10. Shadow Variants (Parallel Rule Simulation)

For every accepted trade, five offline simulations run in parallel with
the live position to estimate what alternative SL/TP/BE/trail rules
would have produced.

```
baseline : SL 1.0 %, TP 1.0 % (50 % partial), BE 0.5 % → 0.3 %, Trail 0.5 %
no_be    : baseline - BE disabled
full_tp  : SL 1.0 %, TP 1.0 % (100 % close), no trail
sl_tight : baseline with SL 0.5 %
sl_wide  : baseline with SL 1.5 %
```

### 10.1 Isolation guarantees

Variants are **pure simulation**. They do not call any Bybit write API.
Their only external read is the shared public price stream (+ kline REST
on restart). They only write into `trade_shadow_variants`. They never
affect the live position, the virtual balance, or any user-facing P&L.

### 10.2 Runtime

Each variant runs up to `MAX_DURATION_SEC` (default 4 hours) or until one
of the terminal outcomes:

| Outcome | Trigger |
|---|---|
| `sl_hit` | Initial SL crossed, no TP |
| `be_hit` | BE-SL crossed after BE move, no TP |
| `tp_trail` | Trailing SL crossed after a partial TP |
| `tp_full` | TP crossed on a 100 %-close variant |
| `timeout` | Budget exhausted with no terminal trigger |

Each step (once per second) updates `best_price`, `worst_price`,
`current_sl`, and the `be_active` / `trailing_active` booleans.

### 10.3 Variants persist past live-trade close

Variants run independently of the real trade status. When the live
position closes after 30 min, the five variants keep running for up to
3.5 h more because the question they answer ("what would the alternate
rule have returned?") is not coupled to the live exit.

To make this possible, `PriceManager` reference-counts subscriptions.
Each variant subscribes on start and unsubscribes on completion; the
live monitor does the same. The underlying WebSocket subscription is
kept alive as long as any caller holds a ref.

### 10.4 Restart recovery (kline replay)

Because variant state (best / worst price, current SL, BE flag, partial
TP flag) is in-memory, a process restart would otherwise destroy it. On
startup:

1. For each pending variant row, fetch 1-minute klines from
   `created_at` to now.
2. Each close is expanded into 60 simulated ticks (1 min = 60 × 1 s).
3. `replay_variant()` steps deterministically through the full history
   using the same `_step` function the live loop uses.
4. If a terminal outcome fires during replay → persist as closed.
5. Otherwise → hand the resulting `VariantState` (with accumulated
   `elapsed_sec`) to `simulate_variant()` which continues live from
   there until its remaining budget elapses.

This removes a legacy `lost_on_restart` outcome from the data set.

### 10.5 Schema

See §13 for the `trade_shadow_variants` table.

### 10.6 Lifecycle diagram

```
 signal accepted
       │
       ▼
 ┌──────────────────────────┐
 │ _spawn_shadow_variants() │     (§10.0 call site, 1 row per variant)
 └──────────┬───────────────┘
            │  × 5 variants
            ▼
 ┌──────────────────────────────────────────┐
 │ _track_shadow_variant(variant_id, …)      │
 │   await price_mgr.subscribe(symbol)       │   (refcounted; §10.3)
 │   await simulate_variant(get_price, …)    │   (§10.2 loop, 1 s tick)
 │   price_mgr.unsubscribe(symbol)  finally  │
 └──────────┬───────────────────────────────┘
            │
     ┌──────┴─────────┬──────────┬──────────┬──────────┐
     ▼                ▼          ▼          ▼          ▼
  sl_hit           be_hit    tp_trail    tp_full    timeout
  (initial SL)    (BE-SL    (trailing   (variant   (4 h budget
                  hit after SL after    = full_tp)  exhausted)
                  BE move)  partial TP)

  ┌─ on every step (`_step` in shadow_simulator.py) ─────────┐
  │  1. update best / worst price (MFE / MAE)                │
  │  2. BE move if fav_pct ≥ be_trigger and BE not yet active│
  │  3. partial TP if tp price crossed → enable trailing     │
  │  4. trail SL = best ∓ trail_pct while trailing_active    │
  │  5. SL hit check (initial / BE / trailing)               │
  └──────────────────────────────────────────────────────────┘

                   bot restart
                        │
                        ▼
 ┌─────────────────────────────────────────────────────────┐
 │ restore_monitors() — for each pending variant row:       │
 │   1. fetch 1 m klines (created_at → now)                 │
 │   2. expand each close to 60 ticks (1 m = 60 × 1 s)      │
 │   3. replay_variant(..., prices=expanded)                │
 │         ├── hit during replay  → VariantOutcome          │
 │         │   └── close_shadow_variant(finalize)           │
 │         └── no hit             → VariantState            │
 │             ├── elapsed < MAX  → spawn _track_shadow_    │
 │             │                    variant(initial_state=) │
 │             └── elapsed ≥ MAX  → finalize as timeout     │
 └─────────────────────────────────────────────────────────┘
```

---

## 11. Shadow Tracking (Rejected-Signal Observation)

Separate from §10. When a signal is **rejected** (low score, or
opposite-direction block), it is inserted into `signal_shadow` and a
60-minute observation task is spawned. The task records:

- MFE/MAE every 2 s
- Mark-price snapshots at 5, 15, 30, 60 minutes
- A terminal label: `would_tp` / `would_sl` / `would_be` / `no_trigger`

The dashboard surfaces these as "if we had taken the rejected trade,
what would have happened?" — a filter-accuracy feedback loop.

On restart, shadow tasks that still have budget are resumed; those past
the 60-min window are marked `lost_on_restart` (this outcome remains in
use here because no price replay is wired into the rejected-signal
path yet).

---

## 12. Trade Context Logging (Retrospective Analytics)

For each executed trade, a parallel row is written into `trade_context`
containing point-in-time derived metrics. The design rule is:

> **Collect; do not filter.** Adding a column is allowed; introducing a
> new automatic filter or a sizing change on the basis of a single
> feature is not.

### 12.1 Point-in-time correctness

All SQL behind `compute_trade_context` uses `WHERE opened_at < ?` and
excludes the current `trade_id`, so retrospective re-computation never
leaks future information. A backfill script
(`scripts/backfill_trade_context.py`) iterates trades in ascending `id`
order and writes each row
under `INSERT OR REPLACE` on the `trade_id` PRIMARY KEY, making the
whole process idempotent.

### 12.2 Column families

- **Rolling behavioral context (SQL-derived from trades + signals):**
  win-rate / average-PnL over the last 10 / 20 / 30 trades, consecutive
  win/loss streaks, daily / hourly counts, score-bucket cohort stats.
- **Per-symbol context:** 24-hour trade count, last-10 WR, minutes since
  previous same-symbol trade.
- **Shadow metrics:** shadow WR over last 20, rejected-signal count in
  last hour, watch-list signal count.
- **Calendar:** hour UTC, day of week, minute of day, weekend flag.
- **SQL-derived from trade row:** `btc_trend_strength` (EMA delta),
  `btc_trend_aligned` (boolean), `atr_pct` (ATR / entry),
  `concurrent_exposure_usd` (SUM of overlapping position sizes
  excluding self).
- **At-open numerics (captured via kwargs at trade-open time):**
  `rsi_value`, `rsi_slope_3`, `oi_change_pct`, `price_change_3`,
  `candle_body_pct`, `candle_close_pos`. Not backfillable → NULL for
  pre-existing rows.

Total: 36 columns at the time of this document.

### 12.3 Fail-safety

`record_trade_context` catches exceptions and logs a warning — it never
interrupts the trade-open flow.

---

## 13. Database Schema

SQLite, WAL mode, one connection with `check_same_thread=False` and a
Python-level `threading.Lock` guarding writes. Forward-only migrations
in `Database._migrate()`.

### 13.1 `trades`

Authoritative record of each executed trade.

```
id               PK AUTOINCREMENT
symbol           TEXT  (Bybit notation)
binance_symbol   TEXT  (original webhook notation)
side             TEXT  ('LONG' | 'SHORT')
entry_price      REAL
exit_price       REAL
qty              REAL
position_size_usd REAL
realized_pnl     REAL
close_reason     TEXT  (e.g. 'tp', 'sl', 'be', 'trail', 'manual')
tier_index       INTEGER
opened_at        TEXT  (ISO-8601 UTC)
closed_at        TEXT
status           TEXT  ('open' | 'closed')
order_id         TEXT  (Bybit OPEN order id)
signal_id        INTEGER (FK signals)
btc_entry_price  REAL   btc_exit_price  REAL
btc_ema_short    REAL   btc_ema_long    REAL
atr_at_entry    REAL   open_positions_count INTEGER
confidence_score INTEGER  score_factors TEXT (JSON)
opposite_trade_id INTEGER  (links paired inverse trades)
mfe_pct          REAL   mae_pct          REAL
close_fills_count INTEGER  exit_price_avg REAL
```

### 13.2 `position_state`

In-flight state for every open position (PK symbol).

```
symbol                PK
side, entry_price, qty, remaining_qty
sl_price, tp_price, sl_type ('protective'|'be'|'trail')
best_price, trailing_active, tp_hit
realized_pnl (running sum from execution fills)
pre_close_pnl
close_fills_count, close_notional_sum, close_qty_sum
max_favorable_price, max_adverse_price
order_id, binance_symbol, opened_at
last_exit_price
```

### 13.3 Supporting tables

| Table | Purpose |
|---|---|
| `signals` | Every inbound webhook; dedup + audit |
| `signal_shadow` | Rejected-signal observation window (§11) |
| `trade_shadow_variants` | Parallel rule simulations (§10) |
| `post_close_prices` | 5/15/30/60-min post-close mark prices |
| `executions` | Per-fill ledger (dedup by `exec_id` UNIQUE) |
| `trade_pnl_deltas` | Audit log of DB-vs-Bybit P&L corrections |
| `trade_context` | Retrospective analytics (§12) |
| `tier_history` | Tier promotion/demotion timeline |
| `config_changes` | Timestamped snapshot of settings changes |

### 13.4 Indexes

```
idx_trades_symbol, idx_trades_status, idx_trades_closed_at
idx_signals_received_at
idx_exec_symbol_time, idx_exec_trade, idx_exec_order
idx_shadow_var_trade, idx_shadow_var_name
idx_trade_context_recorded
idx_pnl_deltas_created
```

### 13.5 Time handling

Every timestamp column stores ISO-8601 UTC with a literal `+00:00`
offset, generated in Python. SQL queries that compare against "now" or
"today" build their boundary in Python and pass it as a parameter —
`datetime('now')` is never used directly because its format differs from
the stored format (YYYY-MM-DD HH:MM:SS vs ISO-8601 with `+00:00`).

### 13.6 Concurrency model (async + threads + SQLite)

The DB layer interacts with three concurrent execution contexts:

1. The single `asyncio` event loop running FastAPI, the monitor tasks,
   WS dispatch, and the audit loop.
2. Worker threads spawned via `asyncio.to_thread(...)` for blocking
   REST calls from monitor tasks and for the Monte-Carlo analytics.
3. FastAPI's own threadpool for any sync endpoint handlers.

Explicit guarantees:

- **One shared SQLite connection** per `Database` instance, opened with
  `check_same_thread=False`. CPython's `sqlite3` module is thread-safe
  at the *statement* level when the underlying library is built
  `SERIALIZED` (default on every modern Linux distro), so a single
  connection can be used from multiple threads simultaneously.
- **All writes serialize through one `threading.Lock`** (`self._lock`).
  Multi-statement write sequences (open trade + position_state +
  signal bind, close trade + tier update) are wrapped in the lock so
  they commit as one logical unit. Readers never take this lock.
- **All reads are single-statement `SELECT`s.** There is no
  application-level read lock and no transactional state is shared
  across reads. Two tasks issuing `SELECT` on the same table
  concurrently is safe; neither sees partial state from an in-progress
  write because SQLite WAL mode gives readers a consistent snapshot
  at statement start.
- **WAL mode (`journal_mode=WAL`)** allows readers and a writer to
  proceed in parallel at the file-format level. The writer-lock in
  WAL is internal to SQLite; the Python-level `threading.Lock` serves
  a different purpose (multi-statement atomicity in application code).
- **No cross-task transaction state.** The codebase never calls
  `BEGIN ... COMMIT` manually. Every write path uses the implicit
  transaction-per-`execute` model plus `commit()` inside the lock.
  This means a reader cannot observe a half-written trade row, and
  two writers cannot interleave statements of a logical unit.

What is **not** guaranteed and would be a bug to rely on:

- Reading a row that is being actively written **inside** an ongoing
  multi-statement write (e.g. between `INSERT INTO trades` and
  `INSERT INTO position_state`) will see only the first of the two
  rows until the lock is released. A reader must tolerate this or
  the write code must collapse the two rows into one transaction
  (which it already does, guarded by `self._lock`).
- Long-running analytics reads (Monte Carlo) hold a SQLite snapshot
  for their duration. This does not block writers (WAL), but the
  snapshot is frozen in time — a concurrent fill that updates
  `realized_pnl` will not appear until the read completes and a new
  query is issued.

Production code follows these patterns uniformly; deviation is a
review flag.

---

## 14. REST API (Dashboard + Integrations)

All endpoints return JSON. The dashboard is polled by the SPA at the
intervals listed in §4.3.

### 14.1 Core read-only

```
GET /                          # Dashboard HTML
GET /health                    # Liveness
GET /api/health-status         # Deep health (WS state, DB reachability)
GET /api/config                # Non-secret runtime config snapshot
GET /api/balance               # Virtual balance + equity
GET /api/positions             # Open positions + live unrealized P&L
GET /api/trades                # Paginated closed trades (filters: symbol, date)
GET /api/stats                 # Aggregate stats (all / 7d / 30d)
GET /api/rolling               # Rolling window metrics
GET /api/btc-correlation       # Per-symbol BTC price correlation
```

### 14.2 Analytics (read-only)

```
GET /api/analytics/mfe-mae
GET /api/analytics/hourly            # Hour-of-day heatmap
GET /api/analytics/per-symbol        # Expectancy per symbol
GET /api/analytics/fee-drag
GET /api/analytics/pnl-distribution
GET /api/analytics/monte-carlo       # CPU-bound, runs via asyncio.to_thread
GET /api/analytics/signal-funnel
GET /api/analytics/hold-time
GET /api/analytics/cluster-risk
GET /api/analytics/confidence-score  # Score histogram
GET /api/analytics/opposite-signals
GET /api/analytics/post-close
GET /api/analytics/day-of-week
GET /api/analytics/concurrent-performance
GET /api/analytics/atr-performance
GET /api/analytics/loss-streaks
```

### 14.3 Audit / ops

```
GET /api/signals?status=…       # Filter: all | ignored | opened | error | duplicate
GET /api/audit-log              # Auto-fix log (24 h)
GET /api/sync-status            # DB vs Bybit matched-trade sync
GET /api/pnl-deltas             # Manual P&L correction ledger
GET /api/trades/{id}/fills      # Per-trade execution list
GET /api/executions/status      # Dedup ring size, seen count
GET /api/shadow-trades          # Rejected-signal shadow (§11)
GET /api/shadow-variants        # Parallel rule sim (§10)
GET /api/equity-curve           # Chart.js series
GET /api/daily-pnl              # Chart.js series
```

### 14.4 Write endpoints

```
POST   /webhook                              # TradingView alert ingest
GET    /api/symbol-map
POST   /api/symbol-map                       # Upsert a mapping
DELETE /api/symbol-map/{binance_symbol}
```

### 14.5 Webhook contract

```
POST /webhook
Header: X-Webhook-Secret: <shared secret>
Body (JSON):
{
    "symbol":  "BTCUSDT" | "BTCUSDT.P" | "BINANCE:BTCUSDT" | ...
    "side":    "buy" | "sell" | "LONG" | "SHORT"
    "price":   <optional, informational>
}
```

The response is always `200 OK` with a JSON body describing the
disposition (`opened`, `ignored`, `duplicate`, `error`). TradingView
treats any non-2xx as a retryable failure, so failures are surfaced in
the payload rather than the status code.

### 14.6 Versioning and compatibility policy

The project is **alpha-stage** (§0.4), so no formal API version
contract is committed. This section documents the informal policy
followed today and what versioning is planned for post-alpha.

**Current state (alpha):**

- **No path prefix.** Endpoints are `/api/...`, not `/api/v1/...`.
- **No version header.** No `Accept-Version` or `API-Version`.
- **Webhook payload accepts a superset.** Unknown fields are silently
  ignored; missing optional fields fall back to defaults. Required
  core fields: `symbol`, `side`.

**Informal compatibility rules followed today:**

- **Read endpoints are additive-only.** New response fields may
  appear; existing fields retain their name, type, and semantics
  across commits unless a rename is explicitly announced in this
  document's changelog.
- **Webhook input is additive-only.** New optional fields may be
  consumed; required fields do not change silently.
- **Write endpoints (`POST`/`DELETE` `/api/symbol-map/...`) are
  considered unstable.** They exist for operator convenience and may
  change shape without notice until auth lands (§21.3).
- **Breaking changes** (rename, type change, removal of an existing
  field, change of semantics) are not introduced silently — they are
  called out in the changelog at the top of this document.

**Planned post-alpha:**

- Path prefix `/api/v1/...` for all dashboard / integration endpoints.
- Versioned webhook payload under a `version` field; the bot rejects
  unknown majors with a clear error body.
- This document's changelog becomes the authoritative diff of the
  external API contract between releases.

Consumers integrating against the current alpha surface should:

1. Treat all endpoints as beta-stability.
2. Not rely on the *order* of fields in JSON responses.
3. Tolerate new optional fields gracefully.
4. Pin to a specific bot commit if a stable contract is required.

---

## 15. Virtual Balance & Tier Manager

### 15.1 Virtual balance

```python
virtual_balance = VIRTUAL_START_BALANCE + sum(realized_pnl from trades)
```

The Bybit account balance is **never** used for sizing decisions. The
live account holds an isolated sub-balance for margining; sizing always
references the virtual figure. This decouples strategy analysis from any
one-off deposits/withdrawals on the real account.

### 15.2 Tier selection

`tier_manager.get_position_size(balance, symbol)` walks
`POSITION_TIERS` and picks the highest `{balance_min ≤ balance}`. It
applies `MAX_POSITION_PER_SYMBOL[symbol_base]` as a ceiling (defaulting
to `MAX_POSITION_PER_SYMBOL["default"]`). The final size is then scaled
by `SCORE_SIZE_MULTIPLIERS[score]`.

### 15.3 Tier promotion / demotion

Promotions require `TIER_UP_MIN_TRADES` consecutive trades in the
current tier (hysteresis). Demotions trigger when drawdown from the
tier's peak balance exceeds `TIER_DOWN_DRAWDOWN_PCT`. Transitions are
written to `tier_history`.

### 15.4 Pre-accounted open fee

On trade open, the expected open-side taker fee
(`entry × qty × FEE_RATE`) is pre-deducted into
`position_state.realized_pnl` so that the running P&L column never
overstates unrealized profit during the trade's lifetime.

---

## 16. Periodic P&L Audit

A loop runs every 5 minutes:

```
fetch Bybit closed-pnl for the last 3 h
for each cp in list:
    find matching DB trade (entry_price + qty, unmatched)
    if diff > $0.50:
        UPDATE trades SET realized_pnl, exit_price, close_reason
        INSERT INTO trade_pnl_deltas
        log WARNING with old / new / diff
```

The `used_bb` set makes the matching 1:1 so a single Bybit record
cannot be attributed to two DB trades. The audit never creates or
deletes a trade — only corrects numeric mismatches on already-matched
rows.

---

## 17. Known Hazards and How They Are Handled

This section captures production-earned defensive patterns.

### 17.1 Failure Modes Matrix — classification by policy

The bot's defensive code follows four explicit policies. Every hazard
below §17.2 classifies under one of these. The matrix answers the
meta-question *"which class of failure is allowed to stop trading, and
which is not?"*.

| Policy | Meaning | When used |
|---|---|---|
| **fail-hard** | Surface the failure; do **not** retry, do **not** auto-recover | Non-idempotent exchange writes where a silent retry could double the side effect |
| **fail-safe** | Close the position / enter a known-safe state immediately | Risk-bearing failures where leaving the system in limbo is worse than exiting |
| **fail-degraded** | Continue with a reduced-capability fallback | Liveness failures where a lower-quality input is still better than none |
| **fail-open** | Continue the pipeline as if the optional step succeeded | Advisory filters whose absence does not change correctness of the core path |
| **fail-reconstruct** | Treat the primary source as unreliable, cross-check against independent sources | Audit-critical data where multiple sources must agree |

| Failure class | Policy | Section | Rationale |
|---|---|---|---|
| Market order timeout / unknown response | fail-hard | §8.2, §7 | `place_market_order` is non-idempotent; a retry risks duplicate position |
| SL-set failure after 3 retries | fail-safe | §6 step 13, §7 | Position without SL is unbounded risk → emergency market close + DB record |
| Confidence-score computation error | fail-open | §9.2 | Score is an advisory filter; trade proceeds with base sizing, event logged as `FILTER BYPASSED` |
| Public WS disconnect | fail-degraded | §4.1 | Exponential-backoff reconnect; `_get_price` falls back to REST mark-price |
| Private WS disconnect | fail-degraded | §7.3 | Reconnect with auth re-handshake; on reconnect, execution dedup + position query reconciles missed events |
| Closed-PnL record missing at close | fail-reconstruct | §7.3 | 3-pass match → accumulated exec P&L → REST `/v5/execution/list` reconstruction |
| Closed-PnL/DB divergence (> $0.50) | fail-reconstruct | §16 | Periodic 5-min audit auto-fixes DB to match closed-pnl; every fix logged to `trade_pnl_deltas` |
| Execution-event replay on WS reconnect | fail-open (dedup) | §7.2 | `execId` ring (max 10 000) absorbs duplicates silently |
| Orphan DB trade (no live Bybit position) | fail-reconstruct | §7 / restore | On restart, close DB entry as "gone" |
| Orphan Bybit position (no DB trade) | fail-safe | restore | Market-close the live position to avoid unmanaged exposure |
| Score-filter rejection | by design, not a failure | §11 | Trade is skipped; shadow-tracking task observes counterfactual |
| Shadow variant state loss on restart | fail-reconstruct | §10.4 | Kline replay deterministically rebuilds state from exchange history |
| Shadow rejected-signal state loss on restart (past 60 min) | fail-hard (giving up) | §11 | Marked `lost_on_restart`; no replay path wired for this table |
| SQLite single-write contention | fail-serialize | §13.6 | All writes serialized through `threading.Lock`; no lost writes, only queued |

Policy choice is *explicit in code*, not incidental. A reader deciding
whether to add retry logic, widen a timeout, or swap an exception for a
log line should first ask: **which policy bucket is this?** and let the
matrix answer.

### 17.2 Hazard catalog

- **Closed-PnL matching must NOT rely on orderId.** Bybit returns the
  CLOSE order id in closed-pnl, but the DB stores the OPEN order id. A
  previous orderId-based matcher mis-attributed revenue. The current
  matcher is 3-pass on `(entry_price, total_qty, timestamp)` with a
  timestamp filter to avoid matching stale records from a previous
  trade on the same symbol and similar entry.
- **2-second sleep before querying closed-pnl.** Bybit takes a moment to
  materialize the closed-pnl record. 0.5 s was insufficient and caused
  the matcher to grab the previous cycle's record.
- **Market order is not retried.** A retry on timeout could create a
  duplicate position. The open path treats "no response" as "unknown"
  and falls through to reconciliation on restart.
- **SL set has a 3× retry + emergency close.** If SL cannot be set, the
  position is market-closed immediately. The open + close are both
  written to the DB with an estimated fee P&L so the virtual balance
  stays consistent.
- **Closed-PnL is the source of truth.** Even when execution events
  sum to a plausible P&L, `_on_position` still queries
  `/v5/position/closed-pnl` on close. WS gaps can drop fills.
- **Execution deduplication.** `_on_execution` tracks `execId` in a ring
  (max 10 000). WS reconnect replays cannot double-count.
- **Monitor REST calls use `asyncio.to_thread`.** Otherwise a slow REST
  call freezes the entire event loop (and hence the dashboard).
- **Partial TP mode.** `tpslMode: "Partial"` must be explicit. The
  default `Full` would close the whole position on TP and the trailing
  branch would never be reached.
- **Orphan close uses the exact qty string from Bybit.** Rounding
  locally can leave sub-step residue (e.g. `0.1` contracts).
- **Shadow cleanup.** Rejected-signal tracking tasks have `finally:
  unsubscribe`. Without this, rejected signals would leak WS subs.
- **Audit 1:1 matching.** `used_bb` set prevents multiple DB trades
  binding to the same Bybit record.
- **`close_trade WHERE id = ?`.** Updating by `symbol + status='open'`
  risked double-closing in edge cases. Close is always by PK.
- **Score computation fail-open.** Filtering bypass is logged
  explicitly (`FILTER BYPASSED`) rather than silent.
- **Post-restart reconciliation.** `restore_monitors` re-subscribes
  price streams, rebuilds `_monitor_position` tasks, replays shadow
  variants (§10.4), closes DB entries whose live position no longer
  exists, and market-closes live positions that are missing from DB.

---

## 18. Testing

`pytest` + `pytest-asyncio` under `tests/`.

### 18.1 Test files and coverage focus

| File | Focus | Criticality |
|---|---|---|
| `test_match_closed_pnl.py` | 3-pass matching, stale-record rejection, 1:1 binding | Critical — P&L correctness |
| `test_scoring.py` | Every score factor, directional squeeze, fail-open path | Critical — filter + sizing |
| `test_shadow_simulator.py` | `_step` transitions, replay determinism, resume-from-state, long/short symmetry | High — recovery correctness |
| `test_trade_context.py` | Phase 1/2 metrics, point-in-time correctness (`opened_at < ?`) | High — analytics integrity |
| `test_audit.py` | Audit loop matching, `used_bb` 1:1 guarantee | High — auto-fix safety |
| `test_tier.py` | Tier promotion hysteresis, per-symbol caps | Medium — sizing |
| `test_price_math.py` | PnL, MFE/MAE, fee math | Medium — numeric |
| `test_rounding.py` | Qty/price step rounding, floor/ceil edge cases | Medium — exchange compat |

Coverage is not enforced at a numeric threshold in CI; the current
strategy is **focused** tests on critical paths rather than line-hit
percentage. Adding a percentage target (e.g. 70 %+ on `bot/` package)
is a deferred task. Running `pytest --cov=bot --cov-report=term` gives
the current snapshot.

### 18.2 What is NOT tested

Explicit so readers do not assume coverage that does not exist:

- **No live-exchange integration tests.** REST is mocked with hand-rolled
  fakes; WS feeds synthetic event streams. A true end-to-end signal →
  fill → close cycle against Bybit testnet is not in the automated
  suite.
- **No load / soak tests.** Write contention, WAL growth under sustained
  write bursts, WS reconnect storms are not regression-tested.
- **No fuzz / property tests** on the signal parser, price math, or
  rounding logic — only example-based unit tests.
- **Dashboard HTTP layer is not covered** (endpoint auth bypasses,
  input validation on `POST /api/symbol-map`, etc.). See §21.2.

### 18.3 Pre-deploy smoke test

Before each deploy, recommended (manual, not yet automated):

1. `pytest -q` — full suite green
2. `ruff check` and `mypy bot/` — no new findings
3. Toggle `BYBIT_TESTNET=true` in a staging env file, boot the bot,
   issue one test webhook, verify open → TP/SL → close flow end-to-end
   against testnet
4. `scripts/compare_bybit.py` on the last 24 h in demo to confirm no
   DB drift was introduced

### 18.4 CI — optional, roadmap

CI is **not required for alpha scope** (see §0.4) and the repository at
1.1 does not include a committed CI workflow. Listed here as a roadmap
item, not a gap. A reader planning to add one should wire: `pytest`,
`ruff`, `mypy`, and (optionally) the testnet smoke into separate stages.
Secrets must not be required for the unit stage; only the testnet stage
needs credentials.

---

## 19. Operational Scripts

- `scripts/backfill_trade_context.py` — retro-compute `trade_context`
  rows in id-ASC order, idempotent via `INSERT OR REPLACE`.
- `scripts/backup_db.py` — hot backup via SQLite's online backup API.
- `scripts/daily_report.py` — CSV export, run from cron at 00:00 UTC.
- `scripts/fix_sync.py` — interactive DB-vs-Bybit reconciliation.
- `scripts/fix_pnl.py` — older P&L correction tool, kept for compat.
- `scripts/compare_bybit.py` — diff DB against Bybit for a date range.
- `scripts/export_analytics.py` — dump analytics endpoints to disk.
- `scripts/walk_forward.py` — walk-forward replay of historical signals.
- `scripts/check.sh`, `check_heartbeat.sh` — cron-friendly liveness.

---

## 20. Observability

### 20.1 In place

- **Log file**: `data/bot.log`, structured text, rotated externally.
- **Alert log**: `data/alerts.log`, append-only, WS reconnect stalls /
  SL failures / audit corrections / uncaught monitor exceptions.
- **Heartbeat file**: `data/heartbeat.json`, updated on each monitor
  iteration. `scripts/check_heartbeat.sh` pages out if staleness exceeds
  a threshold.
- **`/api/health-status`**: WS up/down, DB reachable, last-heartbeat
  age, pending-task counts.
- **Dashboard** as secondary observability surface: live positions,
  virtual balance, trade history, signals tab, audit tab.

### 20.2 Optional roadmap items (deferred past alpha)

> These are **optional upgrades**, explicitly tracked as roadmap
> items — **not gaps in alpha scope** (see §0.4). Current observability
> is sufficient for single-operator, single-host operation. The items
> below become valuable only under multi-instance deployment,
> external-SRE integration, or when the project graduates out of alpha.
> A reader should **not** treat their absence as a maturity issue for
> an alpha-stage internal tool.

- **Metrics (Prometheus / OpenMetrics) — optional, roadmap.** Counters
  for webhook received / opened / ignored / errored, histograms for
  fill-latency (signal → placed) and SL-set latency, gauges for
  open-position count / virtual balance / WS-up. Reduces the need to
  grep logs for trends. Not required for alpha.
- **Distributed tracing (OpenTelemetry) — optional, roadmap.** Spans
  across webhook → score → order → WS fill would help diagnose
  tail-latency issues that logs alone obscure. Not required for alpha.
- **Structured logs (JSON) — optional, roadmap.** Current log format is
  human-readable text; JSON would enable log-aggregator queries without
  custom parsers. Not required for alpha.
- **External alert sink — optional, roadmap.** `alerting.py` currently
  writes to a file. Wiring it to PagerDuty / Opsgenie / a Slack webhook
  is a one-module change; the `alert_error(code, message)` surface is
  already uniform. Not required for alpha.
- **Per-trade timeline view — optional, roadmap.** Dashboard currently
  shows per-trade fills and post-close prices; a full timeline
  (webhook → score → order → fills → SL/TP adjustments → close) would
  make post-mortem analysis faster. Data already exists in `signals`,
  `executions`, `trades`, `trade_pnl_deltas`. Not required for alpha.

### 20.3 Latency observables (to measure once metrics are in place)

Listed here so that a future metrics implementation knows what to
instrument. **No concrete latency numbers are documented** — these
are the *observables*, not measured values. Measurement requires the
Prometheus/OTel upgrade above and is deferred.

| Observable | Meaning | Scope |
|---|---|---|
| `webhook_to_place_ms` | Time from webhook receipt to `place_market_order` call | Per signal |
| `place_to_fill_ms` | Time from order placement to first execution event on private WS | Per trade |
| `sl_set_latency_ms` | Time for `set_trading_stop` (SL) to return OK | Per trade |
| `ws_event_lag_ms` | Server timestamp on WS event minus local receive time | Per event |
| `monitor_loop_jitter_ms` | Deviation of `_monitor_position` iteration from `POSITION_POLL_INTERVAL` | Per open position |
| `close_to_finalize_ms` | Time from `size=0` WS event to `close_trade` commit | Per close |

Claiming specific latency numbers without these observables in place
would be misleading. The bot is bounded by Bybit REST and WS round-trip
time in all critical paths; local computation is not a dominant factor.

---

## 21. Security Notes

### 21.1 In place

- API secrets are **required** at startup (no dev fallback). Missing env
  raises immediately.
- The webhook endpoint validates a shared secret on every request
  (HMAC-free, constant-time compare).
- SQLite file permissions are restricted to the service user.
- No user-supplied input is ever interpolated into a SQL string; every
  query uses parameter binding.
- Outbound REST uses TLS to Bybit endpoints; WebSocket uses WSS.

### 21.2 Known limitations (explicit disclosures)

These are **not** open bugs — they are intentional trade-offs for the
current single-operator, single-host deployment. They are called out so
that downstream integrators cannot assume a security property that is
not there.

- **Dashboard HTTP has no authentication layer.** All `GET /api/*`
  endpoints, including those that expose trade history, virtual
  balance, position state, execution ledger, and config snapshots,
  respond to any request that reaches the port. Anyone who obtains the
  dashboard URL (e.g. the Cloudflare-Tunnel hostname) can read everything.
- **Write endpoints rely on the same perimeter.** The following endpoints
  are **write operations without authentication**:
  - `POST /api/symbol-map` — creates or overwrites a Binance → Bybit
    alias. A malicious alias would re-route a future signal to a
    different market.
  - `DELETE /api/symbol-map/{binance_symbol}` — removes an existing
    alias, breaking a live mapping.
  A reverse tunnel (Cloudflare Tunnel, Tailscale, etc.) is **perimeter
  defense, not identity**: anyone with the URL has full write access.
- **No per-user authorization, no request signing on the dashboard, no
  audit trail of who performed a symbol-map change.** `config_changes`
  logs the *what*, never the *who*.
- **Webhook secret is a shared static string.** No rotation schedule,
  no per-source separation. If leaked, any HTTP client can submit
  valid-looking signals until the secret is rotated and every upstream
  alert is re-issued.
- **Dashboard CORS is unrestricted.** The SPA is served same-origin but
  the API does not reject cross-origin reads.
- **No rate limit on `GET /api/*`.** An unauthenticated reader can
  scrape the full history endpoints.

### 21.3 Mitigations under consideration (not yet implemented)

Listed so an external reader can recognize them as known work, not
oversight. Actual implementation timing is out of scope for this
document.

- Dashboard auth: shared bearer token via `X-Dashboard-Token` header, or
  reverse-proxy-level basic auth, or OIDC if integrating with an IdP.
- Write-endpoint hardening: separate secret for `POST /api/symbol-map`
  and `DELETE /api/symbol-map/{…}`, independent of the webhook secret.
- Webhook secret rotation: support two valid secrets during a rotation
  window.
- Request signing: HMAC over `(timestamp, body)` with short replay
  window instead of static shared secret.
- Audit trail: record `actor` (source IP + auth principal) on every
  write; extend `config_changes` accordingly.

---

## 22. Extension Points for External Analysis

If you are wiring this bot into an external analysis harness:

1. **Trade-level feature extraction** — `trade_context` (36 columns)
   joined to `trades` gives you a point-in-time-safe feature matrix.
   All columns behind `trade_context` are computed against rows strictly
   older than the target trade, so the matrix is free of look-ahead.
2. **Rule comparison** — `trade_shadow_variants` gives per-trade outcome
   under alternative SL/TP/BE/trail rules. Joining on `trade_id` yields
   a wide table of `{baseline, no_be, full_tp, sl_tight, sl_wide}`
   outcomes side-by-side.
3. **Counterfactuals on rejected signals** — `signal_shadow` records
   MFE/MAE and terminal label for every rejected signal, enabling
   filter-quality measurement independent of realized trades.
4. **Execution granularity** — the `executions` table is an append-only
   per-fill ledger (deduplicated via `exec_id UNIQUE`) suitable for
   replaying fills into any exterior simulator.
5. **Audit trail** — `trade_pnl_deltas` and `config_changes` give a
   reproducible timeline of every P&L correction and parameter change.

---

## 23. Glossary

- **MFE / MAE** — maximum favorable / adverse excursion (peak unrealized
  profit / drawdown during trade lifetime).
- **BE** — break-even. Once the price has moved `BE_TRIGGER` in our
  favor, the SL is moved to `entry ± BE_SL_LEVEL` (a small positive
  cushion above fees).
- **Trail** — after a partial TP, the SL follows `best_price ∓
  TRAIL_PCT` for the remaining position.
- **Partial TP** — 50 % of the position closes at TP; the rest remains
  open under the trailing SL.
- **Score** — confidence integer (sum of factors in §9); controls the
  filter and sizing.
- **Shadow trade** — a *rejected* signal we track passively for 60 min.
- **Shadow variant** — a *simulated* trade run in parallel with an
  accepted trade, evaluating alternative exit rules.
- **Virtual balance** — software-tracked equity used for sizing;
  independent of the exchange account balance.
- **Tier** — discrete `{balance_min → position_size}` band; promotions
  require trade-count hysteresis, demotions trigger on drawdown.

---

*End of document.*
