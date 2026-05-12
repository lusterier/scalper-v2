// T-517a2 — single test verifying nav-shadow-aggregate link presence.

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

// Mount at /shadow/aggregate (index landing) — purely client-side, no API
// calls. Stub apiFetch defensively for nav layout.
vi.mock("@/lib/api-client", () => ({
  apiFetch: vi.fn(() => Promise.resolve({ bots: [] })),
}));

describe("__root.tsx left nav (T-517a2)", () => {
  it("renders 'Variant aggregate' link with data-testid=nav-shadow-aggregate + href /shadow/aggregate", async () => {
    const history = createMemoryHistory({ initialEntries: ["/shadow/aggregate"] });
    const router = createRouter({ routeTree, history });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { staleTime: 30_000, retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider> as ReactNode,
    );
    const link = await screen.findByTestId("nav-shadow-aggregate");
    expect(link).toBeInTheDocument();
    expect(link.getAttribute("href")).toBe("/shadow/aggregate");
    expect(link.textContent).toContain("Variant aggregate");
  });
});
