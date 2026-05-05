import { Link, Outlet, createRootRoute } from "@tanstack/react-router";

import { useNavStore } from "@/store/nav";

// Root layout shell per BRIEF §14.2:2052-2054 — left nav + main content.
// T-413 populates nav with 2 NavLinks (Overview + Per-bot live view) per
// OQ-6=B incremental policy; T-414..T-420 append section links per task.
// Per-bot link is disabled until the operator selects a bot anywhere
// (BotSelector on Overview / direct URL nav to /bot/$botId).

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  const lastBotId = useNavStore((s) => s.lastSelectedBotId);
  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <aside className="w-56 border-r border-border bg-card p-4">
        <div className="mb-6 text-lg font-semibold">scalper-v2</div>
        <nav className="space-y-1 text-sm">
          <Link
            to="/"
            className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            activeProps={{ className: "block rounded-md px-2 py-1.5 bg-accent text-foreground" }}
          >
            Overview
          </Link>
          {lastBotId === null ? (
            <span
              className="block cursor-not-allowed rounded-md px-2 py-1.5 text-muted-foreground opacity-50"
              title="Select a bot first"
              data-testid="nav-per-bot-disabled"
            >
              Per-bot live view
            </span>
          ) : (
            <Link
              to="/bot/$botId"
              params={{ botId: lastBotId }}
              className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              activeProps={{
                className: "block rounded-md px-2 py-1.5 bg-accent text-foreground",
              }}
              data-testid="nav-per-bot"
            >
              Per-bot live view
            </Link>
          )}
          <Link
            to="/trades"
            className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            activeProps={{ className: "block rounded-md px-2 py-1.5 bg-accent text-foreground" }}
            data-testid="nav-trades"
          >
            Trade explorer
          </Link>
          <Link
            to="/backtests"
            className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            activeProps={{ className: "block rounded-md px-2 py-1.5 bg-accent text-foreground" }}
            data-testid="nav-backtests"
          >
            Backtest lab
          </Link>
          {lastBotId === null ? (
            <span
              className="block cursor-not-allowed rounded-md px-2 py-1.5 text-muted-foreground opacity-50"
              title="Select a bot first"
              data-testid="nav-strategy-disabled"
            >
              Strategy editor
            </span>
          ) : (
            <Link
              to="/strategy/$botId"
              params={{ botId: lastBotId }}
              className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
              activeProps={{
                className: "block rounded-md px-2 py-1.5 bg-accent text-foreground",
              }}
              data-testid="nav-strategy"
            >
              Strategy editor
            </Link>
          )}
          <Link
            to="/features"
            className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            activeProps={{ className: "block rounded-md px-2 py-1.5 bg-accent text-foreground" }}
            data-testid="nav-features"
          >
            Feature inspector
          </Link>
          <Link
            to="/scoring"
            className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            activeProps={{ className: "block rounded-md px-2 py-1.5 bg-accent text-foreground" }}
            data-testid="nav-scoring"
          >
            Scoring inspector
          </Link>
          <Link
            to="/audit"
            className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            activeProps={{ className: "block rounded-md px-2 py-1.5 bg-accent text-foreground" }}
            data-testid="nav-audit"
          >
            Audit log
          </Link>
          <Link
            to="/settings"
            className="block rounded-md px-2 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
            activeProps={{ className: "block rounded-md px-2 py-1.5 bg-accent text-foreground" }}
            data-testid="nav-settings"
          >
            Settings
          </Link>
        </nav>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
