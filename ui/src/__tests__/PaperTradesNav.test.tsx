// T-516a2 — single test verifying nav-paper-trades link presence.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { routeTree } from "../routeTree.gen";

// Mount at /paper-trades to avoid Overview index route's /api/bots etc.
// queries; the nav lives in the layout shell (__root.tsx), independent
// of the active route. Stub paper-trades list to empty envelope.
vi.mock("@/lib/api-client", () => ({
  apiFetch: vi.fn(() =>
    Promise.resolve({ paper_trades: [], total: 0, limit: 50, offset: 0 }),
  ),
}));

describe("__root.tsx left nav (T-516a2)", () => {
  it("renders 'Paper trades' link with data-testid=nav-paper-trades + href /paper-trades", async () => {
    const history = createMemoryHistory({ initialEntries: ["/paper-trades"] });
    const router = createRouter({ routeTree, history });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider> as ReactNode,
    );
    // TanStack Router resolves routes asynchronously; await the layout
    // shell to render the nav (mirror App.test.tsx pattern).
    const link = await screen.findByTestId("nav-paper-trades");
    expect(link).toBeInTheDocument();
    expect(link.getAttribute("href")).toBe("/paper-trades");
    expect(link.textContent).toContain("Paper trades");
  });
});
