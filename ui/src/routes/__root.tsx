import { Link, Outlet, createRootRoute } from "@tanstack/react-router";
import {
  CandlestickChart,
  ClipboardList,
  Code2,
  FileText,
  FlaskConical,
  LayoutDashboard,
  Radio,
  Settings2,
  SlidersHorizontal,
  Calculator,
} from "lucide-react";

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

  const navLinkBase =
    "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground " +
    "hover:bg-accent hover:text-foreground transition-colors duration-150";
  const navLinkActive =
    "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm " +
    "bg-accent text-primary border-l-2 border-primary pl-[9px]";
  const navLinkDisabled =
    "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm " +
    "text-muted-foreground opacity-35 cursor-not-allowed";
  const sectionLabel =
    "px-2.5 pt-4 pb-1 text-[10px] font-semibold tracking-widest " +
    "text-muted-foreground uppercase";

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <aside className="w-52 border-r border-border bg-card flex flex-col">
        {/* Logo */}
        <div className="px-4 py-4 border-b border-border">
          <div className="font-trading text-sm font-bold tracking-widest text-primary">
            SCALPER-V2
          </div>
          <div className="text-[10px] tracking-widest text-muted-foreground mt-0.5 uppercase">
            Live trading
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          <Link to="/" className={navLinkBase} activeProps={{ className: navLinkActive }}>
            <LayoutDashboard size={15} />
            Overview
          </Link>

          {lastBotId === null ? (
            <span
              className={navLinkDisabled}
              title="Select a bot first"
              data-testid="nav-per-bot-disabled"
            >
              <Radio size={15} />
              Per-bot live view
            </span>
          ) : (
            <Link
              to="/bot/$botId"
              params={{ botId: lastBotId }}
              className={navLinkBase}
              activeProps={{ className: navLinkActive }}
              data-testid="nav-per-bot"
            >
              <Radio size={15} />
              Per-bot live view
            </Link>
          )}

          <Link
            to="/trades"
            className={navLinkBase}
            activeProps={{ className: navLinkActive }}
            data-testid="nav-trades"
          >
            <CandlestickChart size={15} />
            Trade explorer
          </Link>

          <Link
            to="/paper-trades"
            className={navLinkBase}
            activeProps={{ className: navLinkActive }}
            data-testid="nav-paper-trades"
          >
            <FileText size={15} />
            Paper trades
          </Link>

          <Link
            to="/backtests"
            className={navLinkBase}
            activeProps={{ className: navLinkActive }}
            data-testid="nav-backtests"
          >
            <FlaskConical size={15} />
            Backtest lab
          </Link>

          <div className={sectionLabel}>Configure</div>

          {lastBotId === null ? (
            <span
              className={navLinkDisabled}
              title="Select a bot first"
              data-testid="nav-strategy-disabled"
            >
              <Code2 size={15} />
              Strategy editor
            </span>
          ) : (
            <Link
              to="/strategy/$botId"
              params={{ botId: lastBotId }}
              className={navLinkBase}
              activeProps={{ className: navLinkActive }}
              data-testid="nav-strategy"
            >
              <Code2 size={15} />
              Strategy editor
            </Link>
          )}

          <Link
            to="/features"
            className={navLinkBase}
            activeProps={{ className: navLinkActive }}
            data-testid="nav-features"
          >
            <SlidersHorizontal size={15} />
            Feature inspector
          </Link>

          <Link
            to="/scoring"
            className={navLinkBase}
            activeProps={{ className: navLinkActive }}
            data-testid="nav-scoring"
          >
            <Calculator size={15} />
            Scoring inspector
          </Link>

          <div className={sectionLabel}>System</div>

          <Link
            to="/audit"
            className={navLinkBase}
            activeProps={{ className: navLinkActive }}
            data-testid="nav-audit"
          >
            <ClipboardList size={15} />
            Audit log
          </Link>

          <Link
            to="/settings"
            className={navLinkBase}
            activeProps={{ className: navLinkActive }}
            data-testid="nav-settings"
          >
            <Settings2 size={15} />
            Settings
          </Link>
        </nav>
      </aside>

      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
