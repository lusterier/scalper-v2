# ADR-0010: Shadow runtime distinguishes paper from live via `parent_kind` discriminator (BRIEF §2.5/§6.4 deviation)

**Status:** Accepted (2026-05-08, T-511b2a plan-stage Gate 1; operator review per §6.7 — accepted as drafted 2026-05-08)
**Context window:** F5 shadow runtime cluster (T-511b2a foundation + T-511b2 producer + T-512 + T-513 inheritance)
**Authors:** Operator + plan-reviewer (BRIEF §6.4 + §7.4 deviation flagged in T-511b2a plan-reviewer pass 1; OQ-1=B operator decision 2026-05-08 selected paper-mode plno-scope)

## Decision

The F5 shadow runtime — `ShadowWorker` per-variant FSM (T-511b1 shipped consumer half + T-511b2 producer half), schema foundation (T-511b2a migration 0015), OHLC replay restart-recovery (T-512), and rejected-signal observation (T-513) — distinguishes paper-mode parent trades from live-mode parent trades via a **`parent_kind: Literal["live", "paper"]` discriminator** plumbed through three layers:

1. **DB schema** (`shadow_variants.parent_kind: TEXT NOT NULL`, migration 0015 — replaces 0014 FK `parent_trade_id → trades(id)` with discriminator-routed integrity).
2. **Wire envelope** (`ShadowStartPayload.parent_kind: Literal["live", "paper"]`, packages/bus/payloads.py).
3. **Strategy-engine producer mapping** (BotConfig.exchange.mode → ShadowStartPayload.parent_kind: `"paper"` → `"paper"`; `"live"` / `"testnet"` → `"live"`).

This is a deliberate deviation from BRIEF §2.5:268 ("Downstream services cannot distinguish paper from live") narrowed in scope to ONLY the shadow runtime cluster.

## Context

### BRIEF §2.5:268 verbatim

> "If the bot's config has `exchange.mode: paper`, `execution-service` routes requests to the `PaperExchange` adapter instead of Bybit. The `PaperExchange` adapter reads the same `market.ticks.*` stream, simulates fills with a configurable slippage model, and emits `orders.events` on the same subject. **Downstream services (strategy-engine, analytics-api) cannot distinguish paper from live.**"

The invariant lists two specific services as "downstream" — strategy-engine + analytics-api — and frames the contract as: those two services consume `orders.events` without paper/live differentiation.

### Shadow runtime architectural conflict

The F5 shadow runtime cluster (BRIEF §13) introduces a NEW execution-service component (`ShadowWorker`) that subscribes to a NEW internal topic (`shadow.start.<bot_id>`). Per migration 0014 (T-510a shipped 2026-05-07 commit `8716cc0`), the new `shadow_variants` table FK-references `trades.id` — the LIVE trades table.

In v2 reality:
- **Live mode**: `execution-service` writes `trades` rows via `placement_persist.py:404 insert_trade`; FK is satisfied; shadow_variants can reference parent.
- **Paper mode**: `PaperExchange.place_market_order` writes `paper_trades` rows internally (see `packages/exchange/paper/adapter.py:747 insert_paper_trade`); `paper_trades.id` is a SEPARATE BIGSERIAL sequence; no `trades` row exists for paper bots; FK violation if shadow attempts to reference paper_trade_id.

### Operator deployment context

Per `~/.claude/projects/-home-luster-scalper-v2/memory/deployment.md` (operator memory, 2026-05-08):
- v2 multi-service is **NOT deployed anywhere** (no live, no testnet, no shared paper).
- Sibling v1 bot disabled 2026-05-02 (systemd disable + timescaledb container stopped).
- Per OPERATOR_CONTEXT.md:29 — operator's primary v2 trading mode TODAY is paper. Backtest harness reuses same execution paths as paper.

### Design choice

Three viable paths surfaced at T-511b2a plan-stage:

- **A. Live + testnet only** — explicit scope-out of paper from shadow runtime. Paper bots silently emit no `shadow.start.>` events; per-bot `shadow.enabled=true` YAML config is no-op for paper bots. Preserves BRIEF §6.4 invariant verbatim. F5 shadow demo blocked until live deployment OR testnet bot brought up.
- **B. Paper plno-scope (this ADR)** — extend shadow runtime to paper mode via `parent_kind` discriminator + migration 0015 FK relax. Deviates from BRIEF §6.4 letter (shadow runtime distinguishes paper/live), but preserves it for the two listed downstream services (strategy-engine + analytics-api still receive identical OrderEvents per §2.5). Allows operator to test shadow runtime against live paper bots without waiting for live deployment.
- **C. Defer paper support** — T-511b2 ships live-only; paper shadow becomes future task in F6+. Matches A but explicitly time-bounds the deferral.

Operator chose **B** at OQ-1=B (2026-05-08) — primary trading mode is paper; shadow runtime needs to fire there.

## Consequences

### Accepted

- **Shadow runtime IS paper-aware** at three layers: DB schema (parent_kind column), wire envelope (ShadowStartPayload.parent_kind field), strategy-engine producer (BotConfig.exchange.mode → parent_kind mapping). Three-layer plumbing preserves single-source-of-truth (BotConfig.exchange.mode); no other component needs to know.
- **§6.4 narrowed scope**: the invariant "downstream services cannot distinguish paper from live" REMAINS for the two listed services (strategy-engine consuming `orders.events`, analytics-api persisting to `trading_events`). Shadow runtime is a NEW first-class component (BRIEF §13) introduced post-§6.4 spec; the invariant is interpreted as binding on EXISTING listed services, NOT on every component subsequently added.
- **Paper-aware shadow demo path**: operator can run a paper bot with `shadow.enabled=true` + variants → shadow worker fires per parent paper trade → variant outcomes persisted to shadow_variants with parent_kind='paper'. End-to-end testable WITHOUT live deployment.
- **Cross-cutting consistency**: T-511b2 producer + T-512 OHLC replay + T-513 rejected-signal observation all inherit parent_kind discriminator. ADR-0010 binds the cluster.

### Trade-offs

- **Loss of single-table FK referential integrity**: migration 0015 drops `shadow_variants_parent_trade_id_fkey` (FK → trades.id) and replaces with parent_kind discriminator + app-layer integrity. DB layer no longer detects orphan rows where parent_trade_id ∉ {trades.id ∪ paper_trades.id}; cascade-delete on parent trade no longer fires (was T-510b's "cascade-delete-race defensive None return" rationale; now strictly row-not-found semantic).
- **Cross-table writeback complexity**: paper-mode shadow needs to source `paper_trades.id` (paper persistence happens INSIDE PaperExchange.place_market_order). T-511b2 producer plan must extend OrderPlaceResult or add a `select_paper_trade_id_by_open_order_id` helper to extract.
- **Discriminator drift risk**: parent_kind value MUST stay in sync with parent_trade_id source. A bug at strategy-engine producer (e.g., setting parent_kind='live' for paper bot) would write orphan shadow_variants rows that reference paper_trades.id but claim live. Mitigation: T-511b2 acceptance criterion #N MUST verify parent_kind = bot_config.exchange.mode mapping at producer-side; integration test exercises both paths.
- **Downgrade-on-paper-rows risk**: migration 0015 downgrade re-adds FK to trades(id); if shadow_variants contains parent_kind='paper' rows whose parent_trade_id ∉ trades.id, downgrade fails with FK violation. Operator-acknowledged; documented in 0015 docstring + plan-doc T-511b2a WG#2.
- **Future paper_trades hardening**: if F6+ introduces paper_trades cascade-delete semantics (e.g., paper account reset clears paper_trades but keeps shadow_variants), parent_kind='paper' rows would orphan silently. Out-of-scope for F5; flag as forward-pointer.

### Rejected alternatives

- **Alternative A (live + testnet only)**: per operator OQ-1=B decision; operator wants paper shadow today. Rejected at OQ-stage.

- **Alternative C (defer paper to F6+)**: per operator OQ-1=B decision; operator does NOT want to defer. Rejected at OQ-stage.

- **Alternative D (dual-FK + mutually-exclusive CHECK)**: schema design with TWO nullable FK columns + XOR check:

  ```sql
  live_trade_id  BIGINT REFERENCES trades(id) ON DELETE CASCADE,
  paper_trade_id BIGINT REFERENCES paper_trades(id) ON DELETE CASCADE,
  CONSTRAINT shadow_variants_parent_xor CHECK (
    (live_trade_id IS NOT NULL AND paper_trade_id IS NULL) OR
    (live_trade_id IS NULL AND paper_trade_id IS NOT NULL)
  )
  ```

  **Pro**: preserves DB-layer referential integrity; cascade-delete fires on either parent; orphan rows detected at INSERT time.

  **Con**: two nullable columns + XOR constraint is verbose at every read site (helper code branches on NULL detection); migration 0015 would be DESTRUCTIVE per BRIEF §7.4:1192 (renaming + retyping `parent_trade_id` to `live_trade_id` + adding `paper_trade_id` is a multi-step destructive change requiring data migration plan); existing T-510b shipped helpers + T-511b1 shipped consumer would need broader refactor (`ShadowVariantRow` projection + `insert_shadow_variant` signature + shadow_worker.py usage).

  **Quantitative comparison (LOC delta vs Alternative D)**:
  - Alternative B (this ADR — discriminator + no FK): ~70 LOC src counted + ~80 LOC migration + ~190 LOC tests = ~340 LOC.
  - Alternative D (dual-FK + CHECK): ~120 LOC src counted (helper branch logic) + ~130 LOC migration (rename + add + drop + CHECK) + ~280 LOC tests = ~530 LOC.

  Rejected because **app-layer integrity is sufficient** for shadow runtime semantics (parent_kind drives all read/write logic; orphan rows are theoretical because BotConfig.exchange.mode is single-source-of-truth + Pydantic Literal validation at producer + tests cover both paths). LOC overhead 60% higher with limited correctness gain.

- **Alternative E (paper bots write to BOTH `trades` and `paper_trades`)**: PaperExchange.place_market_order would dual-write to live `trades` + paper `paper_trades`. **Pro**: single FK preserved; live/paper distinguishable via `paper_trades` table presence. **Con**: violates §2.5 "Downstream services cannot distinguish paper from live" much more aggressively (analytics-api consumes `trades` table for paper trades — observer CAN distinguish via `paper_trades` JOIN); breaks T-219 cumulative-delta close-flow (paper closed_pnl is from paper_trades; live trades.realized_pnl would double-count); cross-cutting reach into reconcile.py / dispatcher.py / audit.py. Rejected as architecturally invasive.

## Implementation references

- `migrations/versions/0014_shadow_variants.py:84-90` — current FK constraint to drop in 0015.
- `migrations/versions/0015_shadow_variants_relax_parent_fk.py` (T-511b2a; new) — FK drop + parent_kind add per this ADR.
- `packages/db/queries/shadow.py:315-347` (T-510b shipped) — `insert_shadow_variant` gains `parent_kind` kwarg per T-511b2a.
- `packages/db/queries/shadow.py:80-96` — `ShadowVariantRow` dataclass gains `parent_kind` field.
- `packages/bus/payloads.py:36-53` — `ShadowStartPayload` gains `parent_kind: Literal["live", "paper"]` field.
- `packages/exchange/paper/adapter.py:747` — `insert_paper_trade` returns paper_trade_id; T-511b2 producer extracts.
- `services/strategy_engine/app/consumer.py:247` — `_publish_order_request` populates ShadowStartPayload.parent_kind from bot_config.exchange.mode (T-511b2 plumbing; T-511b2a payload schema-only).
- `services/execution/app/shadow_worker.py:154-160` (T-511b1 shipped) — `_run_shadow_variant` insert call gets `parent_kind=payload.parent_kind` propagation.
- BRIEF §2.5:268 — "Downstream services cannot distinguish paper from live" verbatim invariant; this ADR narrows it.
- BRIEF §6.4 — testing workflow note (operator-defensive paper-as-default in CI).
- BRIEF §6.7 — deviation protocol (this ADR's authority).
- BRIEF §7.4:1192 — destructive migration ADR rule (FK drop is destructive; covered here).
- BRIEF §13 — shadow runtime cluster spec (introduced post-§6.4; ADR clarifies invariant scope).
- ADR-0009 — precedent for shadow-cluster cross-cutting deviation ADR pattern (OHLC vs ticks for shadow runtime data stream).

## Supersedes / superseded by

None. Future work: if F6+ introduces a unified parent-trade abstraction (e.g., `trades_unified` view across `trades` + `paper_trades`), revisit via new ADR + migration cluster.
