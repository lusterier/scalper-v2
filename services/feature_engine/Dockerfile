# syntax=docker/dockerfile:1.9
#
# feature-engine service image (§9.3, §18.1, ADR-0002).
#
# Two-stage:
#   1. `builder` — install the frozen workspace environment into
#      /app/.venv using the pinned uv CLI. No runtime Python imports yet.
#   2. `runtime` — slim image, non-root, no build tooling. uvicorn
#      launches the factory from services.feature_engine.app.main.
#
# Image pins inherit market-data-svc's verified set (T-100, 2026-04-25;
# itself inherited from signal-gateway T-015a, 2026-04-22):
# python:3.12.13-slim-trixie + ghcr.io/astral-sh/uv:0.11.7. Bare semver
# per §0.8 / §16.7 (digest pinning deferred per T-009/T-012/T-013a/T-014/
# T-015a/T-100 precedent).

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

# packages/ carries __init__.py + every member's source (recursive COPY
# includes the repo-root packages/__init__.py; ADR-0002 "surprise 2").
# services/__init__.py is load-bearing for the runtime import path
# services.feature_engine.app.main:create_app and must be copied
# explicitly — the feature_engine subdir alone is not enough.
COPY packages/ ./packages/
COPY services/__init__.py ./services/__init__.py
COPY services/feature_engine/ ./services/feature_engine/

# --package scope keeps the image minimal: only scalper-v2-feature-engine
# and its declared workspace + external deps. --no-dev excludes the
# dev-group (pytest, ruff, mypy, alembic, httpx).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --package scalper-v2-feature-engine --frozen --no-dev

# -----------------------------------------------------------------------------

FROM python:3.12.13-slim-trixie AS runtime

# Non-root service user. UID/GID 10003 — distinct from signal-gateway's
# 10001 and market-data-svc's 10002 so per-service log attribution on a
# shared host bind mount (if F1+ adds one) does not conflate the
# services. The shared /var/log/scalper deferral from §18.1 still holds.
RUN groupadd --system --gid 10003 feature \
 && useradd --system --uid 10003 --gid feature --no-create-home --shell /usr/sbin/nologin feature

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/packages /app/packages
COPY --from=builder /app/services/__init__.py /app/services/__init__.py
COPY --from=builder /app/services/feature_engine /app/services/feature_engine

USER feature:feature

EXPOSE 8000

# `python -m uvicorn` so sys.path[0] == /app and dotted imports resolve:
# services.feature_engine.app.main is a regular package (services/ and
# services/feature_engine/ both ship __init__.py). --factory tells
# uvicorn to call create_app() at startup. --loop uvloop per §3.1.
CMD ["python", "-m", "uvicorn", \
     "services.feature_engine.app.main:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--loop", "uvloop"]
