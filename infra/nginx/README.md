# `infra/nginx/`

nginx reverse-proxy operational notes for `compose.yaml` /
`compose.dev.yaml` (brief §2.1, §16.3, §16.6, §18.1). The directory
holds a single-file config (`nginx.conf`) and this README. No
secrets, no bootstrap sidecar needed.

Image pin: `nginx:1.30.0-alpine` per §3.2 exact-pinning against
latest stable at pin time (2026-04-17 upstream upload). `mainline-`
and `stable-` tags both resolve to 1.30.0 as of the pin date —
the new stable branch. Brief §18.1 shows `nginx:alpine` (tag-less,
stale); pin is the same class of bump as `prom/prometheus:v3.11.2`
and `grafana/grafana:13.0.1` in T-013a.

## Routing map

Single server block, `server_name _; default_server`, path-based
dispatch. Hostname policy is enforced at the Cloudflare edge
(cloudflared public hostname → `http://nginx:80`), so duplicating
it in nginx would add a second place to maintain without gain.

| Path                             | Upstream                     | Status at T-014        |
|----------------------------------|------------------------------|------------------------|
| `/healthz`                       | nginx (`return 200`)         | UP (self-liveness)     |
| `/webhook`, `/health`, `/ready`  | `http://signal-gateway:8000` | 502 until T-015        |
| anything else                    | nginx (`return 404`)         | 404 always             |

Rate limiting stays with signal-gateway per §9.1 step 3 (20 req/60s
sliding window per source IP). No nginx-tier throttle — stacking
creates two authoritative limiters with divergent behaviour under
burst. Cloudflare WAF is a third optional layer, configured in the
Cloudflare dashboard, outside repo artifacts.

## Upstream DNS — why variable-based `proxy_pass`

nginx resolves `proxy_pass` upstreams at startup by default. T-014
ships before T-015, so `signal-gateway` isn't yet a resolvable
service on the backend network — a static `proxy_pass
http://signal-gateway:8000;` crashes nginx at boot with `host not
found in upstream`. The config defers resolution to request time:

```nginx
resolver 127.0.0.11 valid=30s ipv6=off;

location ~ ^/(webhook|health|ready)$ {
    set $signal_gateway_upstream http://signal-gateway:8000;
    proxy_pass $signal_gateway_upstream$request_uri;
}
```

`127.0.0.11` is Docker's embedded DNS resolver on user-defined
networks. Variable-based `proxy_pass` + explicit `$request_uri`
(unambiguous with variable upstreams) returns 502 on NXDOMAIN
instead of crashing. Mirrors the T-013a pattern where Prometheus's
`signal-gateway` scrape target reads DOWN until T-015 — same
intermediate-state shape.

## Security headers

Four headers on every response, `always` flag emits on 4xx/5xx
too so 502-until-T-015 responses still assert the posture:

| Header                      | Value                                     |
|-----------------------------|-------------------------------------------|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains`     |
| `X-Content-Type-Options`    | `nosniff`                                 |
| `X-Frame-Options`           | `DENY`                                    |
| `Referrer-Policy`           | `no-referrer`                             |

Not shipped in T-014: `Content-Security-Policy` and
`Permissions-Policy` — both need UI context. F4 dashboard-UI task
extends the set.

## Access logs — JSON to stdout

`log_format json` emits a tight 12-key object per request to
`/dev/stdout`, captured by `docker compose logs nginx`. Keys:
`time`, `remote_addr`, `method`, `uri`, `status`,
`body_bytes_sent`, `request_time`, `upstream_addr`,
`upstream_status`, `upstream_response_time`, `request_id`,
`user_agent`. The upstream trio mirrors the brief §15.3
`webhook_processing_seconds` histogram shape on the nginx tier.

Error log stays default text format; nginx upstream doesn't emit
structured error logs without third-party patches. `error_log
/dev/stderr info;` routes to the same `docker compose logs`
stream. `/healthz` probes are excluded from access logs
(`access_log off` inside the location) so docker healthcheck
traffic (every 10s) doesn't drown the signal.

## Request ID propagation

nginx generates `$request_id` (32-char hex) per request and
forwards it as `X-Request-ID` on all signal-gateway proxy calls.
T-015 can adopt it as a gateway-tier trace ID alongside its own
`correlation_id` (§15.2), or ignore — header name frozen here.

## Production pre-up

None. No bind-mounted data directory (nginx is stateless in this
config), no chown, no `/mnt/data/nginx/`. The config binds
read-only from `./infra/nginx/nginx.conf`; access / error logs
are captured via docker's stdout/stderr streams (the image ships
`/var/log/nginx/access.log → /dev/stdout` and `error.log →
/dev/stderr` as symlinks — verified empirically against the
pinned image).

nginx master runs as uid=0 (root); worker processes drop to
uid=101 (nginx user inside the image). No `--user` override;
matches the T-012 nats container's root-master pattern.

## Ports

| Port | Purpose         | Prod           | Dev overlay    |
|------|-----------------|----------------|----------------|
| 80   | HTTP (plain)    | `127.0.0.1:8080:80` (loopback publish) | same (no dev delta) |

Loopback publish is intentional: cloudflared reaches nginx via
service-name DNS on the backend network (§16.6), so the host
publish is for prod-host debugging (`curl http://127.0.0.1:8080/healthz`)
only, never the LAN. The prod base bind already matches dev
ergonomics — `compose.dev.yaml` adds no override for nginx.

## Healthcheck

```yaml
healthcheck:
  test: ["CMD", "wget", "-q", "--spider", "http://127.0.0.1/healthz"]
  interval: 10s
  timeout: 3s
  retries: 5
  start_period: 5s
```

`nginx:1.30.0-alpine` ships busybox `wget`, `curl`, and `nc`
(verified empirically). `--spider` does a HEAD request, exit 0 on
2xx — matches the T-013a prometheus / grafana healthcheck pattern.
Start period 5s is generous for nginx's cold start (typically
<500ms).

`127.0.0.1` is explicit (not `localhost`): `/etc/hosts` inside
the image maps `localhost` to `::1` first, and `listen 80`
without a bracketed-IPv6 address binds IPv4 only. A `localhost`
probe returns "Connection refused" and pins the service as
`unhealthy`. Sibling services (prom/grafana/nats) use
`localhost` safely because their default listen is dual-stack;
nginx is not.

## Smoke verification

From a dev host after `docker compose -f compose.yaml -f
compose.dev.yaml up -d nginx` (cloudflared intentionally left
out of the dev up set — see `infra/cloudflared/README.md`):

```sh
# Self-liveness, no upstream touched
curl -sS -D- -o /dev/null http://127.0.0.1:8080/healthz
# expect: 200, four security headers present

# Upstream proxy path — 502 until T-015 lands signal-gateway
curl -sS -D- -o /dev/null http://127.0.0.1:8080/webhook
# expect: 502, same four security headers

# 404 fallback
curl -sS -D- -o /dev/null http://127.0.0.1:8080/nonexistent
# expect: 404 "not found"

# JSON access log shape (single request; /healthz is NOT logged)
docker compose logs nginx --since=10s | tail -n 5
# expect: one JSON line per logged request with the 12-key schema
```

## Forward references

- **T-015**: signal-gateway lights up `/webhook`, `/health`,
  `/ready`. The 502 → 200 flip is the nginx-tier analogue of
  T-013a's Prometheus target DOWN → UP flip.
- **T-F3**: analytics-api route addition. Either a second
  `location /api/` block on the same server (path-based split),
  or a second `server` block with its own `server_name` (hostname
  split). F3 decision; T-014 leaves no stub.
- **F4** dashboard UI static serve: replaces the `location /
  { return 404; }` fallback with `root /usr/share/nginx/html;
  try_files $uri /index.html;` + a `./ui/dist:/usr/share/nginx/html:ro`
  bind mount on the nginx service.
- **Cloudflare WAF + Cloudflare Access policies**: prod
  hardening layer, configured in Cloudflare dashboard, no repo
  artifact.
