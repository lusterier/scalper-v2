// T-517a2 — shadow.aggregate.index.tsx contract tests. Symbol picker:
// renders input + Go button, button enabled-state toggles, click navigates
// to /shadow/aggregate/$symbol with WG#1 uppercase normalization.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { routeTree } from "../routeTree.gen";

// Stub apiFetch — index page does NOT call API but the BotSelector and
// other layout pieces may; mock to empty defensive responses.
vi.mock("@/lib/api-client", () => ({
  apiFetch: vi.fn(() => Promise.resolve({ bots: [] })),
}));

function mountAt(path: string) {
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createRouter({ routeTree, history });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider> as ReactNode,
  );
  return { rendered, router };
}

describe("ShadowAggregateIndexPage route (T-517a2)", () => {
  it("renders symbol input + Go button + page title", async () => {
    mountAt("/shadow/aggregate");
    await waitFor(() => {
      expect(screen.getByTestId("aggregate-symbol-input")).toBeInTheDocument();
    });
    expect(screen.getByTestId("aggregate-symbol-go")).toBeInTheDocument();
    expect(screen.getByText("Per-symbol best-variant aggregate")).toBeInTheDocument();
  });

  it("Go button disabled when input empty + enabled when filled", async () => {
    mountAt("/shadow/aggregate");
    await waitFor(() => {
      expect(screen.getByTestId("aggregate-symbol-input")).toBeInTheDocument();
    });
    const button = screen.getByTestId("aggregate-symbol-go") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    const input = screen.getByTestId("aggregate-symbol-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "btcusdt" } });
    expect(button.disabled).toBe(false);
    // Whitespace-only input → button stays disabled.
    fireEvent.change(input, { target: { value: "   " } });
    expect(button.disabled).toBe(true);
  });

  it("clicking Go navigates to /shadow/aggregate/$symbol with uppercased trimmed input (WG#1)", async () => {
    const { router } = mountAt("/shadow/aggregate");
    await waitFor(() => {
      expect(screen.getByTestId("aggregate-symbol-input")).toBeInTheDocument();
    });
    const input = screen.getByTestId("aggregate-symbol-input") as HTMLInputElement;
    // Lowercase + leading/trailing whitespace input — WG#1 must normalize.
    fireEvent.change(input, { target: { value: "  ethusdt  " } });
    fireEvent.click(screen.getByTestId("aggregate-symbol-go"));
    await waitFor(() => {
      // Router resolved to /shadow/aggregate/ETHUSDT (uppercased + trimmed).
      expect(router.state.location.pathname).toBe("/shadow/aggregate/ETHUSDT");
    });
  });
});
