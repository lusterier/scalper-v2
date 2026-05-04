# syntax=docker/dockerfile:1.9
#
# alerting-svc image (T-409, §9.7, §18.1, ADR-0002).
#
# Two-stage:
#   1. `builder` — install the frozen workspace environment into
#      /app/.venv using the pinned uv CLI. No runtime Python imports yet.
#   2. `runtime` — slim image, non-root, no build tooling. uvicorn
#      launches the factory from services.alerting.app.main.
#
# Image pins inherit prior service set (T-309 strategy-engine /
# T-400 analytics-api): python:3.12.13-slim-trixie + ghcr.io/astral-sh/uv:0.11.7.
# Bare semver per §0.8 / §16.7 (digest pinning deferred per prior service
# task precedent).

FROM python:3.12.13-slim-trixie AS builder

# uv donor image — bare-semver tag matched to github.com/astral-sh/uv
# latest. Only /uv + /uvx are forwarded; the stage is otherwise clean.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Workspace metadata first so the resolve step caches cleanly on
# source-only edits.
COPY pyproject.toml uv.lock ./

# packages/ carries __init__.py + every member's source.
# services/__init__.py is load-bearing for the runtime import path
# services.alerting.app.main:create_app.
COPY packages/ ./packages/
COPY services/__init__.py ./services/__init__.py
COPY services/alerting/ ./services/alerting/

# --package scope keeps the image minimal: only scalper-v2-alerting and
# its declared workspace + external deps. --no-dev excludes the dev-group.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package scalper-v2-alerting --frozen --no-dev

# -----------------------------------------------------------------------------

FROM python:3.12.13-slim-trixie AS runtime

# Non-root service user. UID/GID 10007 — distinct from signal-gateway's
# 10001, market-data-svc's 10002, feature-engine's 10003, execution-
# service's 10004, strategy-engine's 10005, and analytics-api's 10006 so
# per-service log attribution on a shared host bind mount (if F1+ adds
# one) does not conflate the services. The shared /var/log/scalper
# deferral from §18.1 still holds.
RUN groupadd --system --gid 10007 alerting \
 && useradd --system --uid 10007 --gid alerting --no-create-home --shell /usr/sbin/nologin alerting

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages /app/packages
COPY --from=builder /app/services/__init__.py /app/services/__init__.py
COPY --from=builder /app/services/alerting /app/services/alerting

# configs/alerts.yaml + configs/alerts/templates/*.j2 mounted at runtime
# via compose volume (read-only); copied here as default for image-only
# deployments. Settings.alerts_yaml_path resolves to /app/configs/alerts.yaml.
COPY configs/ /app/configs/

USER alerting:alerting

EXPOSE 8000

# `python -m uvicorn` so sys.path[0] == /app and dotted imports resolve:
# services.alerting.app.main is a regular package (services/ and
# services/alerting/ both ship __init__.py). --factory tells uvicorn to
# call create_app() at startup. --loop uvloop per §3.1.
CMD ["python", "-m", "uvicorn", \
     "services.alerting.app.main:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--loop", "uvloop"]
