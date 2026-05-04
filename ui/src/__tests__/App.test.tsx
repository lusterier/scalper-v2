import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { cn } from "../lib/utils";
import { routeTree } from "../routeTree.gen";

// 4 smoke tests per WG#11 — verify the scaffold itself is wired correctly.
// Real per-component / per-route coverage lands in T-411..T-420.

function renderApp() {
  const router = createRouter({ routeTree });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

describe("UI scaffold smoke tests", () => {
  it("renders without crashing (RouterProvider + QueryClientProvider)", async () => {
    renderApp();
    // Layout shell from __root.tsx — left nav heading. TanStack Router
    // resolves the route asynchronously on first paint; await via findBy.
    expect(await screen.findByText("scalper-v2")).toBeInTheDocument();
  });

  it("displays the component showcase heading (T-411 rewrite)", async () => {
    renderApp();
    // T-411 rewrote routes/index.tsx as component showcase. T-410's
    // "UI scaffold ready" placeholder text removed; new heading is
    // "Component showcase" per WG#4.
    expect(await screen.findByText("Component showcase")).toBeInTheDocument();
  });

  it("cn utility merges Tailwind classes (last-wins on conflict)", () => {
    // Verifies the shadcn/ui-canonical cn() helper composes clsx +
    // tailwind-merge correctly. cn is hand-written + foundational —
    // every shadcn component depends on it. Real-browser styling
    // validated via T-422 Playwright.
    expect(cn("p-2", "p-4")).toEqual("p-4");
    expect(cn("text-red-500", { hidden: false }, "text-blue-500")).toEqual(
      "text-blue-500",
    );
  });

  it("loads the generated routeTree (T-411 routes will extend it)", () => {
    expect(routeTree).toBeDefined();
    expect(typeof routeTree).toBe("object");
  });
});
