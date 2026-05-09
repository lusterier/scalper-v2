// T-516b — Integration tests verifying ShadowVariantsView mounts
// correctly inside both drill-down routes (/trades/$tradeId + /paper-
// trades/$paperTradeId). Focuses on route-level wiring + parent prop
// pass-through. Component-level behavior covered in
// ShadowVariantsView.test.tsx.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "../routeTree.gen";

const mockFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: unknown[]) => mockFetch(...args),
}));

beforeEach(() => {
  mockFetch.mockReset();
});

function mountAt(path: string): ReturnType<typeof render> {
  const history = createMemoryHistory({ initialEntries: [path] });
  const router = createRouter({ routeTree, history });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider> as ReactNode,
  );
}

const tradeFixture = {
  id: 7,
  bot_id: "alpha",
  signal_id: null,
  open_order_id: 10,
  close_order_id: 11,
  symbol: "BTCUSDT",
  side: "long",
  entry_price: "50000.00",
  exit_price: "51000.00",
  qty: "0.001",
  notional_usd: "50.00",
  realized_pnl: "1.00",
  fees_paid: "0.05",
  close_reason: "tp",
  opened_at: "2026-05-05T10:00:00Z",
  closed_at: "2026-05-05T12:00:00Z",
  status: "closed",
  mfe_pct: 0.025,
  mae_pct: -0.005,
  confidence_score: 0.78,
  meta: {},
};

const variantFixture = {
  id: 1,
  parent_trade_id: 7,
  bot_id: "alpha",
  variant_name: "no_be",
  side: "long",
  entry_price: "50000.00",
  qty: "0.001",
  created_at: "2026-05-05T10:00:00Z",
  terminated_at: "2026-05-05T11:00:00Z",
  terminal_outcome: "tp_full",
  realized_pnl: "5.00",
  mfe_pct: 0.03,
  mae_pct: -0.002,
  meta: {},
  parent_kind: "live",
};

describe("ShadowVariantsView integration (T-516b)", () => {
  it("trades.$tradeId.tsx renders ShadowVariantsView with parentKind='live'", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/7") return Promise.resolve(tradeFixture);
      if (url === "/api/trades/7/shadow-variants") {
        return Promise.resolve({ variants: [variantFixture] });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/trades/7");
    await waitFor(() => {
      expect(screen.getByTestId("shadow-variants-view")).toBeInTheDocument();
    });
    // Parent row from Trade prop + 1 variant row from API.
    expect(screen.getByTestId("shadow-variants-parent-row")).toBeInTheDocument();
    expect(screen.getByTestId("shadow-variants-variant-row")).toBeInTheDocument();
    // URL pin: live route URL was hit.
    const variantsCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && c[0] === "/api/trades/7/shadow-variants",
    );
    expect(variantsCall).toBeDefined();
  });

  it("paper-trades.$paperTradeId.tsx renders ShadowVariantsView with parentKind='paper'", async () => {
    const paperVariant = {
      ...variantFixture,
      parent_kind: "paper" as const,
      parent_trade_id: 42,
    };
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/paper-trades/42") return Promise.resolve({ ...tradeFixture, id: 42 });
      if (url === "/api/paper-trades/42/shadow-variants") {
        return Promise.resolve({ variants: [paperVariant] });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades/42");
    await waitFor(() => {
      expect(screen.getByTestId("shadow-variants-view")).toBeInTheDocument();
    });
    expect(screen.getByTestId("shadow-variants-parent-row")).toBeInTheDocument();
    expect(screen.getByTestId("shadow-variants-variant-row")).toBeInTheDocument();
    const variantsCall = mockFetch.mock.calls.find(
      (c) =>
        typeof c[0] === "string" && c[0] === "/api/paper-trades/42/shadow-variants",
    );
    expect(variantsCall).toBeDefined();
  });
});
