// T-516a2 — paper-trades.index.tsx contract tests. Mirror trades.index
// patterns: empty state + populated rows + row navigate + pagination
// Next + bot filter refetch + status=open URL omit (per buildPaperTradesUrl).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "../routeTree.gen";

const mockFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: unknown[]) => mockFetch(...args),
}));

// BotSelector calls /api/bots; stub a minimal response so it doesn't
// pollute the URL assertions. Real BotSelector tests live elsewhere.
vi.mock("@/components/BotSelector", () => ({
  BotSelector: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (v: string) => void;
  }) => (
    <select
      data-testid="bot-selector"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">All bots</option>
      <option value="alpha">alpha</option>
    </select>
  ),
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

const samplePaperTrade = {
  id: 1,
  bot_id: "alpha",
  signal_id: 100,
  open_order_id: 10,
  close_order_id: 11,
  symbol: "BTCUSDT",
  side: "long",
  entry_price: "50000.00",
  exit_price: "51000.00",
  qty: "0.1",
  notional_usd: "5000.00",
  realized_pnl: "100.00",
  fees_paid: "5.00",
  close_reason: "tp",
  opened_at: "2026-05-05T10:00:00Z",
  closed_at: "2026-05-05T12:00:00Z",
  status: "closed",
  mfe_pct: 0.025,
  mae_pct: -0.005,
  confidence_score: 0.78,
  meta: {},
};

describe("PaperTradesPage route (T-516a2)", () => {
  it("renders empty state when no paper trades match filters", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/paper-trades/")) {
        return Promise.resolve({ paper_trades: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades");
    await waitFor(() => {
      expect(screen.getByText("No paper trades match filters")).toBeInTheDocument();
    });
  });

  it("renders rows when paper_trades non-empty", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/paper-trades/")) {
        return Promise.resolve({
          paper_trades: [samplePaperTrade, { ...samplePaperTrade, id: 2 }],
          total: 2,
          limit: 50,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    const { container } = mountAt("/paper-trades");
    // Wait for pagination row to appear (renders only after data loads).
    await waitFor(() => {
      expect(screen.getByTestId("paper-trades-pagination")).toHaveTextContent(
        "(2 paper trades)",
      );
    });
    // 2 rows present in DataTable tbody.
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
  });

  it("clicking row navigates to /paper-trades/$paperTradeId", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/paper-trades/?")) {
        return Promise.resolve({
          paper_trades: [samplePaperTrade],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }
      // Drill-down navigation triggers /api/paper-trades/1 fetch.
      if (url === "/api/paper-trades/1") return Promise.resolve(samplePaperTrade);
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades");
    await waitFor(() => {
      expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("BTCUSDT"));
    await waitFor(() => {
      expect(screen.getByText("Paper trade #1")).toBeInTheDocument();
    });
  });

  it("Next button increments offset (page advance) — pagination URL", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/paper-trades/")) {
        return Promise.resolve({
          paper_trades: [samplePaperTrade],
          total: 100,
          limit: 50,
          offset: url.includes("offset=50") ? 50 : 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades");
    await waitFor(() => {
      expect(screen.getByText("Next")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Next"));
    await waitFor(() => {
      const nextCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/paper-trades/") &&
          (c[0] as string).includes("offset=50"),
      );
      expect(nextCall).toBeDefined();
    });
  });

  it("status=open filter omits ?from + ?to query params (mirror trades.index buildTradesUrl WG#4)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/paper-trades/")) {
        return Promise.resolve({ paper_trades: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades");
    await waitFor(() => {
      expect(screen.getByTestId("status-filter")).toBeInTheDocument();
    });
    const select = screen.getByTestId("status-filter") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "open" } });
    await waitFor(() => {
      const openCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/paper-trades/") &&
          (c[0] as string).includes("status=open"),
      );
      expect(openCall).toBeDefined();
      const url = openCall?.[0] as string;
      expect(url).not.toContain("from=");
      expect(url).not.toContain("to=");
    });
  });

  it("bot filter change triggers refetch with bot_id query param", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/paper-trades/")) {
        return Promise.resolve({ paper_trades: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades");
    await waitFor(() => {
      expect(screen.getByTestId("bot-selector")).toBeInTheDocument();
    });
    const select = screen.getByTestId("bot-selector") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "alpha" } });
    await waitFor(() => {
      const alphaCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/paper-trades/") &&
          (c[0] as string).includes("bot_id=alpha"),
      );
      expect(alphaCall).toBeDefined();
    });
  });
});
