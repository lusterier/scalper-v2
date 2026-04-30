# ADR-0004: Per-bot Bybit credentials sourced from env vars (incl. sub_account)

Status: accepted
Date: 2026-04-30
Deciders: operator, Claude Code
Supersedes: ADR-0003 §Decision 6 (sub_account source)

## Context

ADR-0003 §Decision 6 specified that the per-bot Bybit `sub_account_id` lives as a column on the `bots` table. At that time, the rate-limiter design needed to pin "where does sub_account come from" so that the wire-protocol contract between T-205 (limiter) and T-208 (BybitV5Adapter) was unambiguous. Schema column was the documented choice.

T-215 (adapter pool composition root) is the first task to actually consume `sub_account` at lifespan startup. At consumption time, the schema-column path requires:

1. A migration adding `bots.sub_account_id TEXT` (forward-only per §N8 + per-migration test_migration.py per H-018 family).
2. A backfill of existing rows (the v2 deployment already has live bots in the bots table per memory `deployment.md`).
3. A new `packages/db/queries/bots.py` reader returning `BotRow` with the new column.

Meanwhile, H-022 (per-bot credentials) already mandates `BOT_<ID>_BYBIT_API_KEY` and `BOT_<ID>_BYBIT_API_SECRET` env vars. The env-var family is the established H-022 source of truth for per-bot Bybit credentials.

Operating constraints at F2 scope:
- Single Ubuntu server, single operator, sub-10 bots.
- Service restart is the lifecycle for picking up bot config changes (no hot-reload).
- Operator already manages per-bot env vars in `secrets.env`; adding one more env var per bot is operationally cheaper than a schema migration + backfill + reader.

## Decision

**For F2 phase: per-bot Bybit `sub_account` is sourced from env var `BOT_<ID>_BYBIT_SUB_ACCOUNT` per-bot, not from a `bots` table column.**

This supersedes the relevant portion of ADR-0003 §Decision 6:

> ~~6. **`sub_account` keying**: KV key uses the bot's `sub_account_id` from the `bots` table column. Multiple bots sharing one sub-account share one bucket per endpoint group.~~

becomes:

> 6. **`sub_account` keying**: KV key uses the bot's sub_account string sourced from env var `BOT_<ID>_BYBIT_SUB_ACCOUNT` per H-022 family. Multiple bots sharing one sub-account string share one bucket per endpoint group. Bot identity (`bot_row.bot_id` from `bots` table) and sub_account identity (env var) are kept distinct: a single sub-account may host multiple bots, mapped via env-var indirection.

The rest of ADR-0003 (3 bucket families, 500ms pause, optimistic CAS, env-var rate limits, DI'd limiter, fail-open) stands unchanged.

## Rationale

- **H-022 family symmetry**: api_key + api_secret are env vars (per H-022); adding sub_account to the same family is the principle-of-least-surprise. Operators already have a mental model of "per-bot Bybit credentials live in env"; sub_account fits that model.
- **No schema change**: avoids migration 0009 + test_migration.py + backfill at F2 scope. Migration cost is non-trivial: each migration ships its own `tests/integration/migrations/test_NNNN_migration.py` round-trip pin, and the bots table already carries production rows for the live v2 deployment per memory `deployment.md`.
- **Service restart as config-change lifecycle**: at sub-10-bot scale with operator-driven deployments, service restart on env-var change is acceptable. Schema column would also require restart (Settings/queries don't hot-reload); env vars are no worse on this dimension.
- **Defends against accidental sub_account sharing**: env-var-per-bot makes "bot alpha and bot beta on the same sub-account" require an explicit duplicate env-var value, which is operationally visible (the operator typed the same string twice). Schema column would let it slip through silently as a normal data state.
- **§0.8 anti-hypothetical**: F5+ may revisit if env-var sprawl becomes painful (e.g., 20+ bots with 7+ env vars each = 140+ secrets to manage). At sub-10 bots, env-var family is the simpler ship.

## Consequences

Positive:
- T-215 ships without a schema migration in its diff.
- Symmetric naming pattern: `BOT_<ID>_BYBIT_API_KEY/SECRET/SUB_ACCOUNT`. Easy to document in `.env.example`.
- T-205 / T-208 wire-protocol contract unchanged (the limiter still receives `sub_account: str`; the source is now env, not a column).

Negative / trade-offs:
- ADR-0003 §Decision 6 is now superseded; readers must consult ADR-0004 for the active source-of-truth on sub_account. Cross-reference is documented in this ADR's Status line and in ADR-0003 itself (amended in T-215 commit to add a "Superseded by ADR-0004 (sub_account source)" pointer at the head).
- Future migration trigger: if bot count exceeds ~20, env-var management cost may exceed schema-migration cost. F5+ may add `bots.sub_account_id` column + reader + migration; at that point the env-var family is removed in a coordinated change.
- Operator must document sub_account values somewhere outside env (the env file is the single source of truth, but a human-readable "bot alpha → sub-acc-xyz" mapping helps debugging). Recommend the bots table's `meta JSONB` carry an informational `sub_account_hint` for dashboard display only — but the load-bearing source is env.

## Alternatives considered

- **Migration 0009 adding `bots.sub_account_id TEXT NOT NULL`**: rejected per F2 scope. Migration cost (test_migration.py + backfill on existing prod bots) is operationally heavier than env-var addition. Reconsidered at F5+.
- **`bots.meta JSONB.sub_account` key**: rejected. JSONB keys are operationally invisible; operators won't know to set them without reading docs. Env vars are conventionally where Bybit credentials live (H-022), so adding sub_account to the same family is the principle-of-least-surprise path.
- **Hardcoded `sub_account = bot_id`** (1:1 mapping): rejected. Multiple bots may legitimately share a single Bybit sub-account (e.g., two strategies on the same testnet sandbox). Hardcoded coupling is a regression vs ADR-0003's explicit "multiple bots may share one sub-account" language.

## Demo / testnet routing clarification

T-215 §F.1 documents the URL routing for Bybit:

| `bots.exchange_mode` | REST URL                            | WS URL                                       |
|----------------------|-------------------------------------|----------------------------------------------|
| `live`               | `https://api.bybit.com`             | `wss://stream.bybit.com/v5/private`          |
| `testnet`            | `https://api-testnet.bybit.com`     | `wss://stream-testnet.bybit.com/v5/private`  |
| `paper`              | (PaperExchange in-process — no URL) | (no URL)                                     |

Per memory `deployment.md`, the v2 production deployment uses a Bybit demo sub-account. Bybit's demo facility is a sub-account inside live infrastructure; the live URLs (`api.bybit.com` / `stream.bybit.com`) are the contracted F2 routing for demo-flagged sub-accounts. No `exchange_mode='demo'` value is added to the bots table at F2 scope — operator's demo deployment uses `exchange_mode='live'` with a demo-flagged `BOT_<ID>_BYBIT_SUB_ACCOUNT` env value. F5+ may add a separate demo URL family if Bybit changes its demo routing model.

## Follow-up tasks

- **T-215** (this ADR's primary consumer): `services/execution/app/pool.py` reads `os.environ[f"BOT_{bot_id.upper()}_BYBIT_SUB_ACCOUNT"]`; passes to `BybitV5Adapter(sub_account=...)` ctor; raises `MissingBotCredentialsError` if env missing.
- **ADR-0003 amendment**: add a one-line "Status note" at the head of ADR-0003 cross-referencing ADR-0004 as superseding §Decision 6. Single-line edit; lands in T-215 diff.
- **`.env.example` extension**: T-215 commit may include a `.env.example` (if exists) addition documenting `BOT_<ID>_BYBIT_SUB_ACCOUNT`. Out-of-scope check at implementation time (file may not exist yet at F2 scope).
- **F5+ migration trigger**: if bot count exceeds operator-comfortable env-var threshold, revisit with a migration ADR.
