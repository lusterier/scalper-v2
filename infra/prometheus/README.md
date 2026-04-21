# `infra/prometheus/`

Prometheus operational notes for `compose.yaml` / `compose.dev.yaml`
(brief §15.3, §16.6, §18.1). The directory holds the single scrape
config (`prometheus.yml`) and this README; no secrets, no bootstrap
sidecar needed.

Image pin: `prom/prometheus:v3.11.2` per §3.2 exact-pinning against
latest stable at pin time.

## Scrape targets

| Job name         | Target                 | Status at T-013a     |
|------------------|------------------------|----------------------|
| `prometheus`     | `prometheus:9090`      | UP (self-scrape)     |
| `grafana`        | `grafana:3000`         | UP                   |
| `signal-gateway` | `signal-gateway:8000`  | DOWN until T-015     |

NATS is intentionally absent — `/varz` returns JSON, not Prometheus
text format. A follow-up task adds a `prometheus-nats-exporter`
sidecar; see `infra/nats/README.md` → Forward references for the
rationale.

## Production pre-up (one-time per host)

```sh
sudo mkdir -p /mnt/data/prometheus
sudo chown 65534:65534 /mnt/data/prometheus
```

`prom/prometheus:v3.11.2` runs as uid=65534 (nobody) — verified
empirically against the pinned image. Without `chown` the container
fails fast with a TSDB write error on first startup.

## Retention

15 days, set via `--storage.tsdb.retention.time=15d` in the compose
`command:` list. Brief is silent on retention; 15d is chosen as a
balance between history depth (week-old incident diagnosable) and
host-disk footprint. Changing it requires a container recreate
(`docker compose up -d prometheus`) since it's a CLI flag, not
part of `prometheus.yml`; hot reload covers scrape config only.

## Hot config reload

`--web.enable-lifecycle` in the compose command enables
`POST /-/reload` to apply `prometheus.yml` changes without
recreating the container:

```sh
docker compose exec prometheus wget --post-data='' \
  -qO- http://localhost:9090/-/reload
```

Use after editing `prometheus.yml` (adding a job, tweaking
intervals). Container recreate also works; reload is faster and
preserves the TSDB cache.

## Smoke verification

From a dev host after `docker compose -f compose.yaml -f
compose.dev.yaml up -d prometheus grafana`:

```sh
# Liveness probe (always-on, independent of --web.enable-lifecycle)
curl -s http://127.0.0.1:9090/-/healthy

# Active scrape targets + per-target health
curl -s http://127.0.0.1:9090/api/v1/targets \
  | jq '.data.activeTargets[] | {job: .labels.job, health}'
```

Expected: `prometheus` and `grafana` report `health: "up"`;
`signal-gateway` reports `health: "down"` until T-015 lands.

## Forward references

- T-015 signal-gateway adds the `/metrics` endpoint that lights
  up the `signal-gateway` job.
- Follow-up task: `prometheus-nats-exporter` sidecar + a `nats`
  scrape entry to cover JetStream metrics.
