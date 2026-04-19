# scalper-v2

Multi-bot crypto-derivatives trading platform. Receives signals from TradingView
webhooks, evaluates them through a per-bot configurable scoring engine, and
executes trades on Bybit. Successor to the v1 SQLite-based scalping bot.

**Status:** Early development. Phase F0 (Foundation) in progress.

## Documentation

All architectural, coding, and process rules live in
[`docs/CLAUDE_CODE_BRIEF.md`](docs/CLAUDE_CODE_BRIEF.md). This is the source of
truth. Read it before making any changes.

Key sections to start with:
- §0 Operating Rules
- §1 Non-Negotiables
- §6 Development Workflow
- §19 Phased Delivery Plan
- §20 Known Hazards Catalog

Current task state: [`TASKS.md`](TASKS.md).
Architecture Decision Records: [`docs/adr/`](docs/adr/).
v1 reference documentation: [`docs/v1/BOT_DOCUMENTATION.md`](docs/v1/BOT_DOCUMENTATION.md).

## Stack

Python 3.12, PostgreSQL 16 + TimescaleDB 2, NATS JetStream, FastAPI, React + Vite,
Docker Compose on Ubuntu 24.04 LTS. Full list in brief §3.

## Quick start (deferred)

Deployment instructions will be added once F0 exit criteria are met. See brief §18.

## License

Private. Not for redistribution.
