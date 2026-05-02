# Session status

## 2026-05-02

**F3 progress: 9/14 done.** Marathon session (~18h active code).

Shipped today:
- T-300..T-308 (scoring foundational types + Migration 0010 + 14 condition variants + resolver + evaluator + YAML loader)
- T-301 hotfix (asyncpg JSONB-as-str → defensive `_decode_jsonb` helper)
- F2 deliverables T-220b/T-221/T-222 (audit job + reconciliation + exit-criteria bundle)
- F2 phase deliverables COMPLETE (T-200..T-222 all closed); E1 manual testnet smoke deferred — local dev compose F2 overlay missing for execution-service/market-data/feature-engine; tracked as F2+ opportunistic.

Master green, ci-fast + ci-full GREEN at HEAD `0a0487f`. 1339 tests passing (+225 from session start at T-220a baseline 1044), 82 skipped (testcontainer-gated env-blocked locally; CI-full runs).

## Next session pick-up

**T-309: strategy-engine service skeleton.** Lifespan composition root for per-bot strategy worker:
- `services/strategy_engine/app/main.py` — FastAPI factory + lifespan with asyncpg.Pool + NatsClient + plugin_registry (load via `load_plugin_registry`) + BotConfig (load via `load_bot_config`) + FeatureResolver injection
- Mirror execution-service T-214 skeleton pattern (already shipped F2 — see services/execution/app/main.py)
- Per-bot env: `BOT_ID` env var → loads `configs/bots/<bot_id>.yaml`
- Health/ready/metrics endpoints
- `services/strategy_engine/Dockerfile`
- ~150 LOC src + ~100 LOC tests per plan estimate

Blocked by: T-308 (shipped). Blocks: T-310 (per-bot signal consumer body), T-311 (multi-bot Docker compose), T-313 (F3 exit-criteria E1 dvoj-bot).

## Watch-outs for next session

- **T-308b backlog stub exists** — ScoringRule field-level Pydantic validation hardening (deferred from T-308 brief-reviewer pass-1 CONCERN#1). Pick up after T-313 or sooner if T-310 surfaces validation bugs in bot YAML.
- **Local dev compose F2 overlay** — execution-service/market-data/feature-engine not yet wired in `compose.dev.yaml`. Blocks E1 manual testnet smoke. F2+ opportunistic; address before F2 phase officially closes.
- **Multi-feature composite/series rules** — T-307 v1 builds RuleContext with single feature per rule; sub-conditions see only that feature. Series conditions return data_missing (T-306 doesn't populate feature_history). T-313 exit-criteria E2/E3 may surface this; plan ahead.
- **applies_when v1 ignored** — T-307 has `# T-307 v1: rule.applies_when ignored — see OQ-1, T-308 follow-up` grep-anchor at evaluator.py:107. T-308 keeps as raw dict pass-through. Narrowing to a Condition union is a future task (post-T-313 or T-308b adjacent).
- **F3 phase still under L-006/L-007 LOC pressure**: T-307 (9% overage authorized) + T-308 (109% over → reduced via Path B Lambda compaction). Future tasks in F3 should pre-emptively check mid-write LOC; condition catalog is large.

## Useful refs

- BRIEF §10.4 evaluator pseudocode + §B.1 alpha.yaml example: `docs/CLAUDE_CODE_BRIEF.md` lines 1729-1783, 2909-2999
- Latest plan: `docs/plans/T-308.md`
- Full F3 14-task roadmap: `TASKS.md` lines 96+ ("Next" section)
