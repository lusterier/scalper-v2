# ADR-0003: NATS KV token-bucket shared rate limiter for Bybit V5 adapters

Status: accepted (§Decision 6 sub_account source superseded by ADR-0004 — see ADR-0004 for the F2 active source-of-truth on per-bot sub_account)
Date: 2026-04-29
Deciders: operator, Claude Code

## Context

§11.4 specifies a shared rate limiter for live exchange adapters:

> - Token bucket in NATS KV `rate_limits`.
> - Keys: `bybit:<sub_account>:orders`, `bybit:<sub_account>:positions`, `bybit:ip:global`.
> - Each call debits one token; refills per Bybit documented limits.
> - Coordinated backoff: on `RateLimitError`, all adapters on the same IP receive a 500ms pause flag published to KV.

The 4-bullet specification leaves several design parameters unspecified — they are architectural decisions that bind the wire-protocol contract between T-205 (`packages/exchange/rate_limiter.py`) and T-208 (`BybitV5Adapter` REST methods consuming it). This ADR records the parameter choices and the consistency-model design needed to make the spec implementable.

Operating constraints:
- Single Ubuntu server (Docker Compose); sub-10 bots; <1000 signals/day.
- Bybit V5 documents per-endpoint limits (orders/positions ~10 req/s; market ~120 req/s on a per-IP basis).
- NATS JetStream KV is the only coordination primitive available per ADR-0001 — no Redis, no PostgreSQL row locks for hot-path coordination.
- §N6 (no globals): the limiter is a constructed component DI'd into adapters, not a module-level singleton.
- §N9 (configurability): any tunable value (refill rate, capacity, pause duration) is a Settings env var, not a literal in src.
- §0.8 (anti-hypothetical): we ship the simplest viable design today; F5+ adaptive header parsing deferred.

The brief's example ADR (§6.3 lines 714-749) addresses the "where does coordination state live" question — that decision is already settled in favor of NATS KV. **This ADR addresses the next layer**: budget tuning, coordinated-pause-flag semantics, KV consistency model under concurrent updates, and the consumer wiring path.

Decisions are taken in plan-doc form per operator instruction 2026-04-29 ("Použiť všetky defaulty" on OQ-1..OQ-6).

## Decision

The shared rate limiter is implemented as a NATS KV token-bucket coordinator with the following design:

1. **Three bucket families** keyed by sub-account and endpoint group, plus one IP-global bucket:
   - `bybit:<sub_account>:orders` — refill 10 req/s, capacity 20 tokens.
   - `bybit:<sub_account>:positions` — refill 10 req/s, capacity 20 tokens.
   - `bybit:ip:global` — refill 120 req/s, capacity 240 tokens.
2. **Coordinated-pause flag** at KV key `bybit:ip:pause`, value = ISO-8601 expiry timestamp; duration 500ms (per §11.4 verbatim, env-tunable).
3. **Optimistic read-modify-write with revision check** for token-debit operations; retry-once-on-conflict, fail-open after 3 conflicts.
4. **All bucket parameters are Settings env vars** (refill rate, capacity, pause duration) per §N9.
5. **Limiter handle DI'd via BybitV5Adapter constructor**; one shared `SharedRateLimiter` instance per adapter pool composition root; PaperExchange does not consume the limiter (no upstream limit).
6. **`sub_account` keying**: KV key uses the bot's `sub_account_id` from the `bots` table column. Multiple bots sharing one sub-account share one bucket per endpoint group.

## Rationale

- **Three buckets per sub-account vs single global bucket** (OQ-1 default A): Bybit V5 enforces independent budgets per endpoint group. A single bucket would either be tuned to the slowest group (wasting headroom on faster ones) or risk RateLimitError on a fast group when a slow one has saturated. Three buckets match what the upstream actually enforces; the cost is 3 KV keys per sub-account vs 1, which is negligible at sub-10-bot scale.
- **`bybit:ip:global` cross-cutting bucket**: Bybit's per-IP limit applies independently of sub-account. A single global IP bucket lets multiple sub-accounts on the same host stop saturating each other before the per-sub-account budget runs out.
- **500ms coordinated pause** (OQ-2 default A): brief verbatim. Empirically Bybit's `Retry-After` header is rarely populated; a fixed conservative pause is simpler and the env var (`RATE_LIMIT_PAUSE_MS=500`) lets the operator tune in production without code change. Adaptive `Retry-After` parsing is F5+ complexity.
- **Optimistic concurrency with retry-once + fail-open** (OQ-3 default A): NATS KV `update(key, value, revision)` is the atomic primitive; revision-mismatch on concurrent update is a known mode under burst. Retry-once-on-conflict is sufficient for sub-10-bot scale (probability of triple-collision ≈ 0). Fail-open after 3 conflicts is consistent with the brief's example ADR-0012 spirit ("if NATS fails, rate limiting fails open"); the upstream Bybit per-IP enforcer is the load-bearing safety net.
- **All params as Settings env vars** (OQ-4 default A): §N9 invariant. Per-bucket capacity + refill rate as `RATE_LIMIT_ORDERS_RATE=10` / `RATE_LIMIT_ORDERS_CAPACITY=20` / `RATE_LIMIT_POSITIONS_RATE=10` / `RATE_LIMIT_POSITIONS_CAPACITY=20` / `RATE_LIMIT_IP_GLOBAL_RATE=120` / `RATE_LIMIT_IP_GLOBAL_CAPACITY=240` / `RATE_LIMIT_PAUSE_MS=500`. Defaults match Bybit docs but tunable for testnet (lower) or future vendor changes.
- **DI via BybitV5Adapter constructor** (OQ-5 default A): mirror T-213b `PaperExchange(pool=...)` pattern. T-215 adapter pool composition root instantiates one shared `SharedRateLimiter` and passes the same instance to every BybitV5Adapter constructed for live bots. PaperExchange ctor does not include the kwarg — no upstream limit exists for in-process simulator.
- **`sub_account` from `bots` table column** (OQ-6 default A): the `bots` table (T-103a F1 ship) carries the operator-assigned Bybit sub-account ID per row. The limiter accepts `sub_account: str` parameter on `acquire(...)`; the caller (T-208 BybitV5Adapter) reads `self._sub_account` set at construction.

## Consequences

Positive:
- **Wire-protocol contract pinned**: T-205 + T-208 + T-215 all consume the same KV key shape, env-var name, and method signature without further re-deciding.
- **Single shared instance per host**: one limiter object DI'd into N adapter instances; no per-adapter state divergence.
- **Operator-tunable in production**: env-var bumps to capacity/rate let the operator respond to upstream limit changes without code change or ADR revision.
- **Fail-open is bounded**: even if the limiter reads stale revision repeatedly and bypasses local enforcement, the upstream Bybit per-IP `RateLimitError` is the load-bearing safety surface; coordinated-pause-flag rebroadcast handles the rate-rebound.

Negative / trade-offs:
- **NATS KV eventual-consistency window** (~1ms local; up to ~50ms on flapping cluster): if a burst of 20+ orders arrives within 1ms across multiple bots, the optimistic-CAS read-modify-write loop will see stale revisions and retry. After 3 retries the local limiter fails open; the adapter then issues the upstream call which Bybit will reject with `RateLimitError`. The pause-flag mechanism then forces a 500ms cooldown across all adapters on the IP, which acts as the recovery surface. **This is acceptable but documented; F5+ might switch to Lua-script-on-Redis for atomic decrement if the failure mode becomes load-bearing**.
- **No per-bot fairness when bots share a sub-account**: bot A bursting can starve bot B if both share `sub_account_id`. F2 spec says one sub-account per bot per H-022; if operators ever consolidate sub-accounts under a single API key, fairness becomes a separate concern. **Out of scope at F2**.
- **F5+ deferred — no adaptive `Retry-After` parsing**: when Bybit changes its rate model (e.g., per-symbol limits, weighted endpoints), the env-var defaults need manual update. Acceptable: F2 doesn't have a continuous-deployment SLA that would require adaptive tuning.
- **Three KV keys per sub-account** + 1 global + 1 pause flag = N×3+2 keys. At 10 bots on N sub-accounts this is ≤32 keys; KV bucket size is not pressured. NATS bucket retention is "forever" per the design (no TTL on rate-limit keys; tokens are refilled by the limiter, not expired by NATS).

## Alternatives considered

- **Single global bucket per sub-account**: rejected per OQ-1 — wastes headroom on the fast endpoint group and risks false RateLimitError on the slow group's saturation point (~10 req/s) bleeding into orders/positions calls.
- **Adaptive `Retry-After`-parsed pause duration**: rejected per OQ-2 — Bybit V5 does not consistently populate this header; conservative 500ms fixed pause is simpler. Re-evaluate at F5+ if observability shows pause is the bottleneck.
- **Fail-closed (RateLimitError on KV unreachable)**: rejected per OQ-3 — cascades a NATS outage into trading downtime. Brief example ADR-0012 explicitly specifies fail-open; this ADR inherits that decision.
- **Hardcoded refill rates and capacities in `rate_limiter.py`**: rejected per OQ-4 / §N9 — Bybit's published limits change occasionally; an env-var swap is cheaper than a code change + redeploy.
- **Module-level `SharedRateLimiter` singleton** (anti-pattern): rejected per §N6 — shared state at module scope breaks tests, hides deps, and prevents per-test instances. Constructor-DI is the only acceptable pattern.
- **Redis token bucket with `INCR`+`EXPIRE` atomic ops**: rejected — adds a dependency for one feature when NATS already meets the requirement at our scale (sub-10 bots). ADR-0001 picked NATS as the only coordination primitive; this ADR honors that.
- **PostgreSQL row-level lock on a `rate_limits` table**: rejected — contention under concurrent bot bursts; row locks at trading-hot-path latency would be ~5-10ms vs NATS KV's ~1ms. ADR-0001 example ADR-0012 already enumerates and rejects this.

## Follow-up tasks

- **T-205**: implement `packages/exchange/rate_limiter.py` per this ADR — `SharedRateLimiter` class with `acquire(sub_account: str, endpoint_group: Literal["orders", "positions"])` async method; KV `update(key, value, revision)` debit-with-CAS; pause-flag check before each acquire; settings ingestion via constructor kwargs.
- **T-208** (BybitV5Adapter REST methods): consume the limiter via DI'd ctor kwarg; call `await limiter.acquire(self._sub_account, "orders")` before `place_market_order` / `cancel_order`; `await limiter.acquire(self._sub_account, "positions")` before `get_positions` / `set_trading_stop` / `set_leverage`.
- **T-215** (adapter pool composition root): instantiate one `SharedRateLimiter` from app-state Settings; pass to every BybitV5Adapter ctor in the `bots`-table loop.
- **T-F2+ NATS stream + KV bucket pre-flight check**: verify `rate_limits` KV bucket exists at execution-service startup per §8.2 line 1278.
