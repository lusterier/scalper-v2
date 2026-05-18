# ADR-0017: `exchange_mode="demo"` routes to dedicated Bybit Demo Trading endpoints + non-gating DEMO advisory (BRIEF §16.5 amendment; ADR-0004 partial supersede)

**Status:** Accepted (2026-05-17/18; operator architecture decisions OQ-1 "Pridať DEMO advisory log" + OQ-2 "Len opraviť komentáre" at the T-549b Gate-1 plan-stage; F6 numbered T-549b, the split sibling of T-549a)

## Context

T-549a made `"demo"` a valid 4th `exchange_mode` (scoring/core/db/bus literals) and routed the 5 strategy-engine risk gates' table-dispatch to the real `trades`/`position_state` realm for demo (a demo bot places REAL Bybit demo-trading orders → real rows, NOT paper-simulated). T-549a deliberately left the execution-service adapter-construction + endpoint-URL half (the L-031 boundary pair `pool.py:316` / `test_pool_live_safeguard.py` `_bot_row`) to T-549b.

ADR-0004 §"Demo / testnet routing clarification" contracted, at F2 scope, that the v2 demo deployment uses `exchange_mode='live'` with a demo-flagged `BOT_<ID>_BYBIT_SUB_ACCOUNT`, reaching Bybit via the **live** URLs (`api.bybit.com` / `stream.bybit.com`) — "F5+ may add a separate demo URL family if Bybit changes its demo routing model." Operator arming surfaced `retCode=10003 API key is invalid`: Bybit Demo Trading is an **isolated** module with its own account/user-id and **dedicated** endpoints — demo-account API keys do not authenticate against the live host. Bybit V5 official docs (bybit-exchange.github.io/docs/v5/demo, verified 2026-05-18): demo REST `https://api-demo.bybit.com`, demo PRIVATE WS `wss://stream-demo.bybit.com` (the adapter appends the standard V5 `/v5/private` path; demo PUBLIC WS uses mainnet `stream.bybit.com` — irrelevant: the adapter pool uses only the private WS).

BRIEF §16.5 (live-mode safeguard) requires `BOT_CONFIRM_LIVE=yes` + a loud `LIVE MODE ENGAGED` warning + Telegram alert for `exchange.mode: live`; "Testnet and paper modes do not require this." Demo is the new case: real order lifecycle on an isolated account, **no real capital**.

## Decision

1. **`exchange_mode="demo"` routes to dedicated Bybit Demo Trading endpoints** — REST `https://api-demo.bybit.com`, private WS `wss://stream-demo.bybit.com/v5/private` — via `pool.py` constants `_BYBIT_DEMO_REST_URL` / `_BYBIT_DEMO_WS_URL` and a 3rd branch in `_construct_bybit_adapter`; `build_adapter_pool` adds `"demo"` to the Bybit-adapter allowlist (`("live","testnet","demo")`). The per-bot credential path (`BOT_<ID>_BYBIT_API_KEY/SECRET/SUB_ACCOUNT`, H-022/ADR-0004) is unchanged — only `base_url`/`ws_url` vary by mode; demo requires **demo-account** API keys (created from Bybit's Demo Trading section, NOT live keys).
2. **Demo safeguard = non-gating `DEMO MODE ENGAGED` advisory** (operator decision OQ-1). `_check_live_mode_safeguard` logs a WARNING `DEMO MODE ENGAGED` for demo (no `BOT_CONFIRM_LIVE` env-gate, no NATS publish / no Telegram) then returns — before the `!= "live"` early-return. Demo trades a real demo account so it gets a startup advisory for operator visibility, but is **not gated** like live (no real capital at risk). testnet/paper keep their silent bypass; the live path is byte-unchanged.
3. **This partially supersedes ADR-0004 §"Demo / testnet routing clarification"** — the demo=live-URL F2 contract is retired (it remains as the point-in-time F2 record; ADR-0004 head Status-note + this ADR carry the active truth — mirrors the ADR-0003↔ADR-0004 head-pointer precedent, ADR-0004 body byte-unchanged). This is exactly the "F5+ may add a separate demo URL family" path ADR-0004 anticipated, not an ungoverned deviation.

## Consequences

- BRIEF amended via §6.7 (4 sites): §7.2 bots DDL comment + the OrderRequest `Literal` + §B.1 alpha.yaml `mode:` comment all gain `demo`; §16.5 gains a 4th bullet ("Demo mode does not require `BOT_CONFIRM_LIVE` … logs a `DEMO MODE ENGAGED` advisory (no Telegram)"). §2.5 (paper→PaperExchange data-flow), §2.6 (backtest), §2890 (H-024 SL-tolerance), §2908 (ExecutionDispatcher paper-skip) are deliberately-bound L-026 NON-sites (demo→Bybit is a distinct path; demo correctly takes the default non-paper dispatched branch — no edit needed; rationale recorded, not a silent miss).
- `configs/bots/demo.yaml` flips `mode: live`→`demo` + header rewrite; `compose.yaml` two stale "Bybit demo uses the live API endpoint" comment blocks corrected (comments-only per OQ-2; the `BOT_CONFIRM_LIVE: ${BOT_DEMO_CONFIRM_LIVE:-}` wiring is left byte-unchanged — now inert for demo but retained for any future `mode:live` bot; comments are not YAML data → the `test_compose_strategy_engine_services.py` demo↔smoke mirror-pin is unaffected, L-032-safe).
- `pool.py` module docstring + `_check_live_mode_safeguard` docstring rewritten; §N4 pins: `test_bybit_demo_bot_constructed_with_demo_urls` (demo URLs, no `BOT_CONFIRM_LIVE`) + `test_safeguard_logs_DEMO_MODE_ENGAGED_for_demo` (advisory emitted, no error/publish/raise) + the L-031 `_bot_row` Literal extended.
- `docs/runbooks/ops.md` discharges the T-549a-bound MANDATORY ops-runbook trigger (ExchangeSection.mode schema change; precedent T-544 DISCHARGED-with-recording): new-bot template + `mode: demo` doc (demo-account keys, no `BOT_CONFIRM_LIVE`, distinct endpoint, real `trades`/`position_state`).
- No Alembic migration — `bots.exchange_mode` is `sa.Text()` (no PG enum DDL; T-549a established this); §N8 N/A. No §20 hazard touched (URL string routing + advisory log + one allowlist token; H-001/H-012/H-022/ADR-0004-creds path verbatim-unchanged).
- math-validator OUT-of-scope behaviourally (`services/execution/` IS in the Gate-4 path so it runs, but the diff is URL strings + a log call + an enum token — zero Decimal/P&L/indicator math; `placement.py`/`pnl/`/`trades.py` not in diff).
- The F5 §A+§B Live-ready sign-off is NOT reopened — F6 additive post-MVP per ADR-0015; demo endpoint was an operator-acknowledged routing gap, now resolved.

## Rejected alternatives

- **Keep ADR-0004 demo=live-URL** — operator arming proved it wrong (`10003`); Bybit Demo Trading is isolated with dedicated endpoints. The ADR-0004 contract explicitly anticipated this revisit.
- **Demo silent-bypass exactly like testnet (no advisory)** — operator rejected at OQ-1 in favour of a `DEMO MODE ENGAGED` advisory: demo places real orders on a real (isolated) account; startup visibility is worth one WARNING line, while a Telegram alert / env-gate would be live-grade ceremony for a no-real-capital mode.
- **Remove the now-inert `BOT_DEMO_CONFIRM_LIVE` compose wiring** — operator rejected at OQ-2 (comments-only): removing it mutates compose structure → forces a same-commit `test_compose_strategy_engine_services.py` mirror-pin + dev-overlay change (L-032 regression class, T-547 precedent) for no functional gain; the wiring is harmless (the safeguard ignores it for demo) and retained for any future `mode:live` bot.

## Relationship to ADR-0015 / ADR-0016 / ADR-0004

Under F6 (ADR-0015) as numbered T-549b — an operator-directed post-MVP fix under the ADR-0015 §70-81 decision-C standing basis (operator-approved split T-549→T-549a/b). ADR-0017 is a **technical / brief-amendment ADR** (sibling-mechanism of ADR-0016 — the operator-decision ADR created under T-542 despite decision-C's "no per-item ADR"; decision-C governs *phase admissibility*, not whether a technical decision warrants its own ADR). **Partially supersedes ADR-0004** (§"Demo / testnet routing clarification" only); does NOT supersede ADR-0015/0016. Extends BRIEF §16.5/§7.2/§8/§B.1 via the §6.7 ADR brief-amendment mechanism.

## Cross-references

BRIEF §16.5 / §7.2 / §8 (OrderRequest) / §B.1 / §6.7 / §2.5-2.6 (bound-out); ADR-0015 (F6; decision-C); ADR-0016 (sibling operator-decision-ADR precedent under F6); ADR-0004 (superseded-in-part; the anticipated "F5+ separate demo URL family" path); ADR-0003 (head-pointer supersede convention precedent); T-549a (foundation half); L-026 (site-set discipline) / L-031 (the deferred boundary pair) / L-032 (compose-comment / config↔test blast-radius).

## Relevant paths

- `services/execution/app/pool.py` — `_BYBIT_DEMO_REST_URL`/`_BYBIT_DEMO_WS_URL` constants, `_check_live_mode_safeguard` demo branch + docstring, `_construct_bybit_adapter` 3-way, `build_adapter_pool` allowlist, module docstring
- `configs/bots/demo.yaml` — `mode: demo` + header
- `compose.yaml` — execution-service + strategy-engine-demo comment blocks (comments-only; env lines byte-unchanged)
- `docs/CLAUDE_CODE_BRIEF.md` — §7.2 bots DDL, §8 OrderRequest `Literal`, §16.5, §B.1 alpha.yaml
- `docs/adr/0004-bot-credentials-env-var-source.md` — head Status-note pointer
- `docs/runbooks/ops.md` — new-bot template + `mode: demo` doc (T-549a-bound MANDATORY trigger discharge)
- `services/execution/tests/test_pool.py` / `test_pool_live_safeguard.py` — §N4 pins
