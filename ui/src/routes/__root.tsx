import { Outlet, createRootRoute } from "@tanstack/react-router";

// Root layout shell per BRIEF §14.2:2052-2054 — left nav + main content.
// T-411 component library will populate the nav with section links.
// Top bar (bot selector + time range + CEST/UTC toggle + connection
// indicator) lands in T-411 / T-412.

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <aside className="w-56 border-r border-border bg-card p-4">
        <div className="mb-6 text-lg font-semibold">scalper-v2</div>
        <nav className="space-y-1 text-sm text-muted-foreground">
          <div>(navigation populated in T-411)</div>
        </nav>
      </aside>
      <main className="flex-1 p-6">
        <Outlet />
      </main>
    </div>
  );
}
