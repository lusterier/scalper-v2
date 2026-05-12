// T-517b2 — single test verifying nav-shadow-rejected link presence.

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

// Mount at /shadow/rejected to avoid Overview index route's queries; the
// nav lives in the layout shell (__root.tsx), independent of the active
// route. Stub shadow-rejected list to empty envelope.
vi.mock("@/lib/api-client", () => ({
  apiFetch: vi.fn(() =>
    Promise.resolve({ rejected: [], total: 0, limit: 50, offset: 0 }),
  ),
}));

describe("__root.tsx left nav (T-517b2)", () => {
  it("renders 'Rejected signals' link with data-testid=nav-shadow-rejected + href /shadow/rejected", async () => {
    const history = createMemoryHistory({ initialEntries: ["/shadow/rejected"] });
    const router = createRouter({ routeTree, history });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider> as ReactNode,
    );
    const link = await screen.findByTestId("nav-shadow-rejected");
    expect(link).toBeInTheDocument();
    expect(link.getAttribute("href")).toBe("/shadow/rejected");
    expect(link.textContent).toContain("Rejected signals");
  });
});
