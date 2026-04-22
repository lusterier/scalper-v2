# `infra/cloudflared/`

Cloudflare Tunnel operational notes for `compose.yaml` (brief
§2.1, §16.3, §16.6, §18.1). Directory holds this README only — no
config file. The service runs in **env-var token mode**, so
ingress rules live in the Cloudflare dashboard, not in the repo.

Image pin: `cloudflare/cloudflared:2026.3.0` per §3.2 exact-pinning
against latest stable at pin time (2026-03-09 upstream upload).
Cloudflare uses calendar versioning (`YYYY.M.PATCH`); the next
stable lands as `2026.4.0+` and Dependabot / the monthly-image
pass (§16.7) picks it up. Brief §18.1 shows
`cloudflare/cloudflared:latest` — same staleness pattern as the
other §18.1 image sketches (T-009 timescale, T-012 nats, T-013a
prom/grafana precedent).

## Operating mode — env-var token, not `config.yml`

Two supported modes: (a) env-var token, (b) explicit
`config.yml` with an `ingress:` block + `credentials-file`. T-014
ships (a):

- Ingress rules are configured in the Cloudflare dashboard
  (Zero Trust → Networks → Tunnels → the tunnel → Public
  Hostnames). Each hostname routes to `http://nginx:80`.
- The compose service needs only `TUNNEL_TOKEN` in
  `/etc/scalper-v2/cloudflared.env` — no `credentials-file`, no
  `infra/cloudflared/config.yml`.

Trade-off acknowledged: ingress rules are **not versioned in
git**. The alternative — mode (b) — keeps `infra/cloudflared/config.yml`
as the source of truth for hostname → backend routes. It's a
one-task follow-up if operational experience shows drift
between dashboard and repo expectations.

## Operator pre-up (one-time per host)

1. In the Cloudflare dashboard, create a named tunnel (Zero
   Trust → Networks → Tunnels → Create a tunnel → Cloudflared).
   Name it something like `scalper-v2-tunnel`.
2. Copy the generated `TUNNEL_TOKEN` (long JWT string) from the
   "Install and run a connector" step.
3. Install on the host:

   ```sh
   sudo install -d -m 0750 /etc/scalper-v2
   printf 'TUNNEL_TOKEN=%s\n' '<token from step 2>' \
     | sudo install -m 0600 -o root -g root /dev/stdin \
       /etc/scalper-v2/cloudflared.env
   ```

4. In the tunnel's **Public Hostnames** tab, add the hostname
   route(s) — typically one for `signal-gateway` webhook ingress
   (operator-chosen subdomain under their owned domain). Service
   URL: `http://nginx:80`. Path blank (catch-all); nginx does
   the path dispatch downstream.
5. Bring the service up:

   ```sh
   docker compose -f compose.yaml up -d cloudflared
   docker compose logs --tail=30 cloudflared
   # expect: "Connection ... registered" (tunnel attached to Cloudflare edge)
   ```

   The dashboard's tunnel row flips to **HEALTHY** within ~10s.

## TLS posture

HTTPS terminates at the Cloudflare edge (brief §16.3). Traffic
from cloudflared → nginx is plain HTTP over the `backend`
compose network. No certs inside the compose project, no
per-service TLS handshake, no hostname verification against a
custom CA.

## No healthcheck — divergence from §18.4

The `cloudflare/cloudflared:2026.3.0` image is distroless
(`User=65532:65532`, no shell, no `wget`, no `curl`, no `id` —
verified via `docker image inspect`). A docker-compose
`healthcheck.test` needs an executable inside the container; the
only one available is `cloudflared` itself. Usable probes
(`cloudflared tunnel info`, `--metrics` endpoint) require
network egress to Cloudflare or a second shell to `wget` a
local port — neither reachable from inside this image.

Coverage comes from three sources instead:
- `restart: unless-stopped` keeps the process alive if it
  crashes.
- Cloudflare dashboard's tunnel health indicator shows green /
  red from the Cloudflare edge's perspective.
- `docker compose logs cloudflared` captures connection state
  (registered / reconnecting / disconnected).

If a docker-native healthcheck becomes load-bearing later
(e.g., for compose `depends_on: condition: service_healthy`
chaining from some downstream service), the workaround is a
custom image wrapping cloudflared with `busybox wget` and a
`--metrics` probe. Not needed for T-014.

## Ports

None published. The service is pure egress — outbound QUIC /
HTTP/2 to the Cloudflare edge — and its only inbound-like
interface is the backend network name `cloudflared`, which no
other service dials. The backend network is used solely so
cloudflared can reach `nginx` via service-name DNS.

## Dev environment — cloudflared is prod-only

Running cloudflared in dev requires a real Cloudflare tunnel
token and a registered domain, neither available on a dev
laptop. `compose.dev.yaml` carries a **one-line patch** —
`cloudflared: env_file: !reset null` — so `docker compose
config` on a dev host renders cleanly. The base already declares
`env_file: required: false`, but compose treats EACCES on the
root-owned `/etc/scalper-v2/` parent as a hard error separate
from the "file missing" case that `required: false` handles;
the `!reset` drops the reference entirely on dev. Matches the
T-009 `secrets: !reset null` pattern for `pg_password`.

With the stanza in place, the operator runs a selective `up`:

```sh
docker compose -f compose.yaml -f compose.dev.yaml up -d \
  postgres nats nats-init prometheus grafana nginx
```

The service list names every F0 service **except**
`cloudflared`. No compose profiles, no `COMPOSE_PROFILES=` env
var gating — dev overlay is a patch layer and the `up`
invocation names what to start.

## Forward references

- **T-015**: when signal-gateway lands and `/webhook` returns
  200 instead of 502, the external hostname routed through
  cloudflared → nginx → signal-gateway is usable end-to-end. A
  synthetic TradingView webhook becomes the first real
  external-traffic integration test.
- **T-F3**: analytics-api adds a second public hostname (or a
  path on the first); cloudflared dashboard config extends.
- **Cloudflare Access policies** (F4+): SSO / identity-based
  gating in front of the dashboard UI. Configured in the
  Cloudflare dashboard alongside the tunnel; no repo artifact.
- **Config-file mode** migration (optional follow-up): adds
  `infra/cloudflared/config.yml` with an explicit `ingress:`
  block + `credentials-file` path, `TUNNEL_TOKEN` unset,
  compose service gains a second bind mount + a
  `tunnel run <name>` command form.
