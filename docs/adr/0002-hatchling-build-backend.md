# ADR-0002: Use hatchling as the workspace build backend

Status: accepted
Date: 2026-04-19
Deciders: operator, Claude Code

## Context

§3.1 names `uv` as the package manager and dependency resolver for the monorepo but leaves the PEP 517 build backend unspecified. The workspace is declared at the repo root via `[tool.uv.workspace]` with members in `services/*` and `packages/*`, and uses a flat layout (`packages/core/__init__.py`, `packages/observability/__init__.py`, …) rather than the `src/` convention.

T-006 (`packages/observability`) was the first workspace member to declare external runtime dependencies (`structlog`, `prometheus-client`). Local mypy then failed to import those modules, which surfaced the underlying gap: `uv sync --group dev --frozen` installs only root-project dependencies, not the runtime dependencies of workspace members. Making `uv sync --all-packages --group dev` work requires that each member with deps be *installable* — which, in PEP 517 terms, means it must declare a `[build-system]`. Every future member with external deps (T-007 asyncpg, T-008 nats-py, and the services in later phases) will hit the same wall; picking a backend once now avoids solving the same problem three more times.

Options considered:
- `hatchling`
- `setuptools`
- `poetry-core`

## Decision

Use **`hatchling`** as the PEP 517 build backend for every workspace member that declares external dependencies (starting with `packages/core` and `packages/observability`). Members with no external deps stay "virtual" (no `[build-system]`) until their first external dep is added.

## Rationale

- **`uv` default.** Choosing the tool's own default minimises configuration drift and means `uv init` / `uv build` behave as documented without per-project overrides.
- **PEP 621 native.** Metadata (`name`, `version`, `description`, `dependencies`, …) stays in the single `[project]` table already used by each member — no parallel `[tool.*]` metadata block to keep in sync.
- **Zero-config for flat layout.** For `packages/observability/__init__.py` the only configuration needed is a single `[tool.hatch.build.targets.wheel] packages = ["observability"]` line (and analogous for `core`); hatchling then builds a wheel that installs the module at the expected import path. Verified empirically per §0.8 by building a wheel with `uv build` and inspecting it with `python -m zipfile -l dist/*.whl` before merge.
- **Installable-but-minimal.** Adding `[build-system]` only where it is needed (i.e., where the member has external runtime deps) keeps the repo's build footprint as small as the current scale demands, while leaving the same pattern ready to apply to future members without another ADR.

## Consequences

Positive:
- `uv sync --all-packages --group dev --frozen` installs each workspace member as an editable wheel, so runtime imports (`import structlog`, `import prometheus_client`) resolve during local mypy and in CI without further glue.
- The same pattern — one `[build-system]` stanza + one `[tool.hatch.build.targets.wheel] packages = […]` line — copies verbatim to every future member that gains external deps (T-007, T-008, services).
- No JVM, no extra tooling process: hatchling is a pure-Python backend already pulled in transitively by `uv`.

Negative / trade-offs:
- Each member that grows a runtime dep must now remember to add the three-line `[build-system]` + hatchling packages stanza; forgetting it is a silent "virtual member" regression. Mitigated by the pattern being uniform and the CI-fast `uv sync --all-packages` failing loudly if a declared dep can't be resolved for a member that ought to be installable.
- We are coupled to hatchling's specific quirks around flat-layout discovery; if we later move to a `src/` layout or adopt a different backend, every member's `pyproject.toml` changes. Considered acceptable because the change is mechanical and the current flat layout is the brief's chosen shape.
- `uv build` of the full workspace now produces one wheel per installable member, slightly increasing CI artefact surface; not shipped anywhere in F0 so the cost is local only.

## Alternatives considered

- **`setuptools`.** Ubiquitous and well-understood; every Python developer has touched it. Rejected: its modern PEP 621 configuration still carries legacy edges around flat-layout auto-discovery (`packages = find:` vs `[tool.setuptools.packages.find]`), needs more boilerplate than hatchling for the same result, and is not uv's default — choosing it would mean deliberately swimming against the tool's grain for no gain at our scale.

- **`poetry-core`.** A reasonable, modern backend with a mature ecosystem around it. Rejected: it assumes Poetry-flavored pyproject.toml shape (`[tool.poetry]` metadata tables) that conflicts with the PEP 621 `[project]` tables `uv` expects and that we already use across every member. Mixing the two styles inside one workspace would be a constant source of drift; picking hatchling keeps every member on a single, standard metadata schema.

## Follow-up tasks

- T-006 unpark: once T-020 merges to master, `git stash pop` resumes T-006 with the observability modules + a now-working `uv sync --all-packages --group dev --frozen`; the stashed code carries the `[build-system]` stanza for `packages/observability` that T-020 establishes, so the resume is a no-conflict pop-and-finish.
- T-007 (`packages/db`, asyncpg pool factory): will add its own `[build-system]` + `[tool.hatch.build.targets.wheel] packages = ["db"]` to `packages/db/pyproject.toml` as part of its scope, following the pattern this ADR establishes — no further ADR needed.
- T-008 (`packages/bus`, NATS client wrapper): same pattern as T-007 for `packages/bus/pyproject.toml`.
- Future services (T-015 signal-gateway and later): each adopts the same three-line hatchling stanza at the point it declares its first external runtime dep.
