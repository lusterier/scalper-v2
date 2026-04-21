# `infra/nats/`

NATS JetStream operational notes for `compose.yaml` /
`compose.dev.yaml` (brief §8.1, §8.2, §16.6, §18.1, §19 F0 bullet 2).

Image pins: `nats:2.12.7-alpine` (server) and
`natsio/nats-box:0.19.3` (one-shot bootstrap sidecar, bundles the
`nats` CLI + `jq`). Per §3.2 exact-pinning against latest stable
at pin time.

## Bootstrap architecture

The `nats-init` sidecar runs `bootstrap.sh` on every `up`, gated
on `nats` reaching `HEALTHY`. Idempotent: existing resources are
updated via `stream update` / `kv edit`, missing ones created.
Exits 0 on success; on any CLI error the non-zero exit surfaces
in `docker compose logs nats-init`.

### Streams — filesystem as source of truth

One JSON file per stream under `streams/`, passed verbatim to
`nats stream add|update --config`. The CLI unmarshals into the
server's `StreamConfig`, so the JSON files **are** the topology —
no separate reference doc.

Durations (`max_age`, `duplicate_window`) are nanosecond `int`s,
required by the Go `time.Duration` unmarshaller. The human-readable
form lives in each stream's `description` field and is visible via
`nats stream info <name>`.

### KV buckets — inline CLI

`nats kv add` has no `--config` flag equivalent (verified against
nats-box 0.19.3). The three §8.2 buckets are explicit shell calls
in `bootstrap.sh`:

| Bucket           | TTL  | History | Purpose                                 |
|------------------|------|---------|-----------------------------------------|
| `config_runtime` | 0    | 1       | hot config per bot (§2.1, §8.2)         |
| `rate_limits`    | 10s  | 1       | cross-bot rate limiter state (§8.2)     |
| `feature_latest` | 0    | 1       | latest feature value per symbol (§8.2)  |

Asymmetry with streams is intentional: the §8.2 set is closed at
three. A fourth bucket is a scope change (TASKS entry or ADR),
which is the right gate to revisit the inline-vs-filesystem split.

## Production host pre-up

One-time per host, before the first `docker compose up`:

```sh
# JetStream store directory. No `chown` needed — nats:2.12.7-alpine
# runs as uid=0 (root) inside the container (verified against the
# pinned image). chmod 0700 keeps the host-side directory private.
sudo mkdir -p /mnt/data/nats/jetstream
sudo chmod 0700 /mnt/data/nats/jetstream
```

Then:

```sh
docker compose -f compose.yaml up -d nats nats-init
docker compose ps nats              # expect STATE=running, HEALTH=healthy
docker compose logs nats-init       # expect "nats-init: topology applied"
```

`nats-init` is one-shot (`restart: "no"`); it exits 0 after
applying topology. Re-running `up` re-executes it — safe by
design (idempotent).

## Storage subpath — why `/mnt/data/nats/jetstream`

Brief §18.1 lists the mount verbatim as `/mnt/data/nats`. The
compose file uses `/mnt/data/nats/jetstream` instead: JetStream
owns that subpath exclusively, leaving `/mnt/data/nats/` as the
service root for sibling artifacts (backups, archives) that may
land in F1+. Mirrors `/mnt/data/postgres`'s intent — one root per
service, content under typed subpaths.

## Ports

| Port | Purpose           | Prod        | Dev overlay         |
|------|-------------------|-------------|---------------------|
| 4222 | Client protocol   | unpublished | `127.0.0.1:4222`    |
| 8222 | HTTP monitoring   | unpublished | unpublished         |

Prod reachability is fenced by the `backend` network (§16.6). 8222
stays internal in dev too — T-013 Prometheus scrape runs over the
backend network, not via a host publish.

## §8.2 gap — `duplicate_window` per stream

Brief §8.2 leaves the server-side dedup window unspecified per
stream. Values below are chosen from the publish pattern of each
subject and fulfill the **server-side half** of the T-008b
`Nats-Msg-Id` dedup contract. Consumer-side `exec_id` dedup in
execution-service (H-009) is separate and lands in F2 via a
`DedupingConsumer` base class.

| Stream            | `max_age` | `duplicate_window` | Rationale                                  |
|-------------------|-----------|--------------------|--------------------------------------------|
| `SIGNALS`         | 7d        | 2m                 | TradingView webhook retries span minutes   |
| `ORDERS`          | 30d       | 30s                | place/amend retries are tight              |
| `MARKET_TICKS`    | 1h        | 60s                | WS reconnect replay window                 |
| `MARKET_OHLC`     | 7d        | 60s                | bar-close retries                          |
| `FEATURES`        | 7d        | 60s                | feature-compute republish after gap        |
| `AUDIT`           | 365d      | 24h                | daily batch writers, broad idempotency     |
| `TRADING_EVENTS`  | 365d      | 60s                | mirrors `ORDERS` payload, long retention   |
| `ALERTS`          | 90d       | 60s                | alert emitter retries                      |

## Smoke verification

From a dev host after `docker compose -f compose.yaml -f
compose.dev.yaml up -d nats nats-init`:

```sh
# JetStream reachable
nats --server nats://127.0.0.1:4222 server check jetstream

# All 8 streams present
nats --server nats://127.0.0.1:4222 stream ls

# All 3 KV buckets present
nats --server nats://127.0.0.1:4222 kv ls

# Round-trip inspect a stream
nats --server nats://127.0.0.1:4222 stream info SIGNALS --json \
  | jq '.config | {name, max_age, duplicate_window, description}'
```

If `nats-init` failed, `docker compose logs nats-init` prints the
failing CLI invocation; fix the JSON or the inline call and
`docker compose up -d nats-init` to re-converge.

## Forward references

- T-013 wires Prometheus to scrape `http://nats:8222/varz` over
  the backend network.
- T-015 signal-gateway will publish to `signals.*` (§8.1) using
  the T-008b `NatsClient` wrapper's `Nats-Msg-Id` header, relying
  on `SIGNALS.duplicate_window = 2m` for server-side dedup.
- F2 execution-service adds the `DedupingConsumer` base class
  resolving H-009 — independent from server-side
  `duplicate_window`.
- A fourth KV bucket or a ninth stream is a scope change: raise
  a TASKS entry and revisit the inline-vs-filesystem split in
  `bootstrap.sh` then.
