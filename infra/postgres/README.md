# `infra/postgres/`

PostgreSQL 16 + TimescaleDB operational notes for `compose.yaml` /
`compose.dev.yaml` (brief §18.1, §19 F0 bullet 2).

Image pin: `timescale/timescaledb:2.26.3-pg16` (TSL edition — enables
compression policies per brief §18.3). Spec §18.1 lists `2.15-pg16`;
bumped per §3.2 exact-pinning against latest stable at pin time
(2026-04-14 upstream release).

## Production host pre-up

One-time per host, before the first `docker compose up`:

```sh
# Data and backup directories, owned by the postgres container UID.
# The TimescaleDB image runs postgres as UID 999 (GID 999), not $USER.
sudo mkdir -p /mnt/data/postgres /mnt/data/backups/postgres
sudo chown 999:999 /mnt/data/postgres /mnt/data/backups/postgres
sudo chmod 0700 /mnt/data/postgres

# DB password file (brief §18.1 Docker secret). Mode 600, owned by root.
sudo install -d -m 0750 /etc/scalper-v2
printf '%s' '<strong-random-password>' \
  | sudo install -m 0600 -o root -g root /dev/stdin /etc/scalper-v2/pg_password
```

Then:

```sh
docker compose -f compose.yaml up -d postgres
docker compose ps postgres           # expect STATE=running, HEALTH=healthy
```

Expected cold-start time to `healthy`: ~15-25s (image pull excluded).
`start_period: 30s` in the healthcheck covers `initdb` on first boot;
subsequent starts flip healthy within ~5s.

## Development host

No `/mnt/data` or `/etc/scalper-v2` setup required. Populate
`POSTGRES_PASSWORD` in the project `.env` (see `.env.example` for the
`change-me-dev-only` default), then:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d postgres
psql postgresql://scalper:change-me-dev-only@127.0.0.1:5432/scalper -c 'SELECT 1;'
```

The dev overlay swaps the `/mnt/data/postgres` bind for a named docker
volume (`postgres_data`), drops the `/backups` mount, clears the
Docker-secret reference, and publishes `127.0.0.1:5432:5432`. See
`compose.dev.yaml` for the exact merge deltas.

To wipe dev state: `docker compose -f compose.yaml -f compose.dev.yaml down -v`.

### Dev first-use checklist — validate the merged config

Before the first `up`, sanity-check the effective merged config on the
target host (Compose v2 sequence merge is append-by-default; we rely on
`!override` / `!reset` tags in `compose.dev.yaml` — validating once per
host catches Compose-version surprises):

```sh
docker compose -f compose.yaml -f compose.dev.yaml config
```

Expected in the output:

- `services.postgres.volumes` has **exactly one entry**, the named
  volume `postgres_data:/var/lib/postgresql/data`. No `/mnt/data/*`
  bind mounts remain.
- `services.postgres.secrets` key is **absent or empty** — no
  `pg_password` reference leaks through from the base.
- `services.postgres.environment.POSTGRES_PASSWORD` is populated with
  the value from `.env` (not the literal `${POSTGRES_PASSWORD}`).
- `services.postgres.environment.POSTGRES_PASSWORD_FILE` is empty
  string (so the image entrypoint's "mutually exclusive" guard doesn't
  fire).
- `services.postgres.ports` contains `127.0.0.1:5432:5432`.

If any of the above drifts, the local Compose version may predate the
`!override` / `!reset` tag support; bump to Compose v2.24+ and re-run.

## TimescaleDB extension — created by Alembic, not by this compose

The image ships the `timescaledb` shared library, but does **not**
`CREATE EXTENSION` in the `scalper` database. That happens in the
Alembic migration that first needs hypertables (T-010 wires up Alembic;
T-011 creates the `signals` hypertable — the extension is created
there via `CREATE EXTENSION IF NOT EXISTS timescaledb`). No `initdb`
init-sql hook here; extension ownership stays with migrations.

## Backups mount — placeholder for pgBackRest (F1+)

The `/mnt/data/backups/postgres:/backups` mount ships in the prod base
so pgBackRest (brief §18.2) can drop archives there without a compose
edit later. pgBackRest itself is out of F0 — no sidecar container, no
cron, no config. The dev overlay drops the mount entirely; there is
nothing to back up in dev.

## Why no published port in production?

The `postgres` service is reached only by other compose services on
the `backend` network (T-010 Alembic runner, T-015 signal-gateway,
later T-F2/F3 services). External access is deliberately absent;
diagnostic access on the prod host is via `docker compose exec
postgres psql …`, not via a published TCP port.
