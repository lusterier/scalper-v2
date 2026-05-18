# ADR-0001: Use NATS JetStream as the message bus and KV store

Status: accepted
Date: 2026-04-19
Deciders: operator, Claude Code

## Context

The brief (§2.1, §3.3) requires a message bus for inter-service communication across `signal-gateway`, `market-data-svc`, `feature-engine`, `strategy-engine`, `execution-service`, `analytics-api`, and `alerting`. §8.1–8.2 enumerates the required streams (`SIGNALS`, `ORDERS`, `MARKET_TICKS`, `MARKET_OHLC`, `FEATURES`, `AUDIT`, `TRADING_EVENTS`, `ALERTS`) and KV buckets (`config_runtime`, `rate_limits`, `feature_latest`).

Operating constraints:
- Single Ubuntu server (Docker Compose), single operator, no dedicated SRE.
- Scale target: sub-10 bots, <1000 signals/day, single-region deployment.
- Delivery requirement: at-least-once with durable consumers and explicit acks.

Options considered:
- NATS JetStream
- Apache Kafka
- Redpanda

## Decision

Use **NATS JetStream** as both the message bus and the KV store.

## Rationale

- **Smaller operational surface.** Single Go binary, no JVM, no ZooKeeper/KRaft quorum, no separate schema registry. Configuration fits in `infra/nats/server.conf` plus `infra/nats/streams.yaml`.
- **KV store included natively.** The buckets required by §8.2 (`config_runtime`, `rate_limits`, `feature_latest`) are first-class JetStream KV objects — no need to add Redis as a second dependency.
- **Throughput headroom is overwhelming for our scale.** NATS handles millions of messages per second on commodity hardware; we need <1000 signals/day plus market-data and feature-update traffic that stays well within single-node capacity.
- **Subject hierarchy fits the design.** Wildcard subjects (e.g., `orders.events.fill.alpha`, `features.updated.btcusdt.15m`) map cleanly to the per-bot / per-symbol routing we need.
- **At-least-once via durable consumers + explicit acks** matches the delivery guarantee the brief assumes throughout (§8.5, §20 hazard catalog).

## Consequences

Positive:
- One infrastructure component covers both pub/sub and shared KV state, simplifying `compose.yaml`, monitoring, and backup procedure.
- No JVM tuning, GC pauses, or ZooKeeper failure modes to learn.
- Stream and KV bootstrap is declarative YAML, fitting the operator's "configure once, redeploy" workflow.

Negative / trade-offs:
- Smaller community and ecosystem than Kafka — fewer Stack Overflow answers for niche issues; we may have to read source.
- No equivalent of Kafka Connect or ksqlDB. If we later want stream-to-DB connectors or stream SQL, we build them ourselves.
- Cross-region replication, mirroring, and tiered object storage are weaker than Kafka's. Not relevant at current scale; would constrain a future multi-region fan-out and trigger a re-evaluation.
- We are coupled to JetStream's specific durability semantics; switching later would touch every service that uses `packages/bus`.

## Alternatives considered

- **Apache Kafka.** Industry standard, deep ecosystem, strongest durability and replication story. Rejected: the operational surface (JVM heap tuning + ZooKeeper or KRaft quorum management + optional schema registry) is disproportionate for a single-server, sub-10-bot deployment with one operator. Additionally, Kafka has no built-in KV, so `rate_limits` / `feature_latest` / `config_runtime` would force a second dependency (Redis or PG), eroding the simplicity that would otherwise justify the heavier broker.

- **Redpanda.** Single binary, Kafka-API compatible, no JVM, no ZooKeeper — closes most of Kafka's operational gap. Rejected: still has a larger memory and CPU footprint than NATS at idle, and **has no built-in KV store**. Losing the §8.2 KV buckets would mean adding Redis (or PG) anyway, which defeats the operational-surface argument that is the entire reason for preferring Redpanda over Kafka in the first place. NATS wins by collapsing both roles into one process.

## Follow-up tasks

- T-008: `packages/bus` — NATS client wrapper, `MessageEnvelope` Pydantic model, publish/subscribe helpers.
- T-012: Docker Compose service for NATS JetStream with `infra/nats/server.conf` and stream bootstrap for `SIGNALS`, `ORDERS`, `MARKET_TICKS`, `MARKET_OHLC`, `FEATURES`, `AUDIT`, `TRADING_EVENTS`, `ALERTS` plus the three KV buckets.

---

> **T-553 ORDERS_DLQ stream provisioned (2026-05-18):** T-216a shipped the
> `orders.dlq.<bot_id>` dead-letter subject + the `placement.py`
> DLQ-publish-on-failure path (OQ-3/OQ-8) WITHOUT the §8.2 / `infra/nats/streams`
> stream lockstep; the failure safety-net itself failed on the first real
> order-request failure (`error="nats: no response from stream"`, surfaced
> during T-552 demo-bot arming). T-553 adds a **dedicated forensic
> `ORDERS_DLQ` stream** (`subjects:["orders.dlq.>"]`, `max_age` 365d mirroring
> AUDIT/TRADING_EVENTS, `duplicate_window` 0 — per operator decision; a DLQ is
> a failure-forensics record, not live order flow). It is an
> application-level handler-explicit DLQ, NOT a `max_deliver` consumer
> (H-003 / T-216a OQ-3 forbid order auto-retry-from-replay). The token-bucket
> design + stream topology model are **unchanged** — a defect-completion of
> T-216a's shipped-but-unprovisioned intent. **No new ADR** — pre-governed by
> ADR-0015 decision-C (F6 task T-553). The "Follow-up tasks" T-012 line above
> enumerates T-012's original 8-stream scope and is a point-in-time record —
> deliberately **NOT rewritten** (`ORDERS_DLQ` post-dates it via T-553;
> editing it would falsely attribute the stream to T-012, L-027). Cross-ref:
> the ADR-0003:96 / §8.2 NATS-resource pre-flight-CHECK (which would have
> *detected* this missing stream) remains a separate deferred sibling, NOT in
> T-553 scope.
