import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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

const sampleTrade = {
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

function defaultFetchImpl(url: string): Promise<unknown> {
  if (url === "/api/bots/") {
    return Promise.resolve({ bots: [] });
  }
  if (url.startsWith("/api/trades/?")) {
    return Promise.resolve({ trades: [sampleTrade], total: 1, limit: 50, offset: 0 });
  }
  return Promise.reject(new Error(`unmocked URL: ${url}`));
}

describe("Trades route (T-414)", () => {
  it("renders DataTable with trades from /api/trades/", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/trades");
    await waitFor(() => {
      expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    });
  });

  it("status filter appends ?status= (status='all' omits param)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/trades");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/trades/?"),
      );
      expect(call).toBeDefined();
      // status=all by default → URL must NOT contain "status="
      expect(call?.[0] as string).not.toContain("status=");
    });

    // Switch to "closed"
    const select = screen.getByTestId("status-filter") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "closed" } });
    await waitFor(() => {
      const closedCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" && (c[0] as string).includes("status=closed"),
      );
      expect(closedCall).toBeDefined();
    });
  });

  it("time range filter uses .toISOString() Z-suffix per §N1", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/trades");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/trades/?"),
      );
      expect(call).toBeDefined();
      const url = call?.[0] as string;
      expect(url).toMatch(/from=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
    });
  });

  it("status='open' OMITS ?from= + ?to= (per BLOCKER #2 / WG#4)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/trades");
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled();
    });

    const select = screen.getByTestId("status-filter") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "open" } });
    await waitFor(() => {
      const openCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).includes("status=open"),
      );
      expect(openCall).toBeDefined();
      const url = openCall?.[0] as string;
      // Negative assertion per WG#4 — status=open MUST NOT include from/to.
      expect(url).not.toContain("from=");
      expect(url).not.toContain("to=");
    });
  });

  it("symbol filter appends ?symbol= to query URL", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/trades");
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled();
    });

    const symbolInput = screen.getByPlaceholderText(/Symbol/) as HTMLInputElement;
    fireEvent.change(symbolInput, { target: { value: "BTCUSDT" } });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).includes("symbol=BTCUSDT"),
      );
      expect(call).toBeDefined();
    });
  });

  it("renders custom pagination block + DataTable internal footer is NOT rendered (per WG#7)", async () => {
    // Backend returns 2 pages worth of trades; tests pageSize=50 batch.
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/bots/") return Promise.resolve({ bots: [] });
      if (url.startsWith("/api/trades/?")) {
        return Promise.resolve({
          trades: Array.from({ length: 50 }, (_, i) => ({
            ...sampleTrade,
            id: i + 1,
          })),
          total: 120,
          limit: 50,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked URL: ${url}`));
    });
    mountAt("/trades");
    await waitFor(() => {
      expect(screen.getByTestId("trades-pagination")).toBeInTheDocument();
    });
    // Custom block rendered with Page X of Y indicator.
    expect(screen.getByTestId("trades-pagination").textContent).toMatch(/Page 1 of 3/);
    // Backend returned 50 rows + pageSize=50 → DataTable's internal
    // pagination footer ("Page X of Y") must NOT be rendered (only one
    // pagination block exists). Our custom block has data-testid; the
    // DataTable internal footer has no testid but uses the same "Page"
    // text pattern. Find them both — only one should match.
    const allPageMatches = screen.getAllByText(/Page \d+ of \d+/);
    expect(allPageMatches).toHaveLength(1);
  });

  it("Previous button advances offset query param + disables on first page", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/bots/") return Promise.resolve({ bots: [] });
      if (url.startsWith("/api/trades/?")) {
        return Promise.resolve({
          trades: Array.from({ length: 50 }, (_, i) => ({
            ...sampleTrade,
            id: i + 1,
          })),
          total: 120,
          limit: 50,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked URL: ${url}`));
    });
    mountAt("/trades");
    await waitFor(() => {
      expect(screen.getByTestId("trades-pagination")).toBeInTheDocument();
    });
    const prevBtn = screen.getByRole("button", { name: "Previous" });
    expect(prevBtn).toBeDisabled();
    const nextBtn = screen.getByRole("button", { name: "Next" });
    fireEvent.click(nextBtn);
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).includes("offset=50"),
      );
      expect(call).toBeDefined();
    });
  });

  it("renders 'No trades match filters' empty state", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/bots/") return Promise.resolve({ bots: [] });
      if (url.startsWith("/api/trades/?")) {
        return Promise.resolve({ trades: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked URL: ${url}`));
    });
    mountAt("/trades");
    await waitFor(() => {
      expect(screen.getByText("No trades match filters")).toBeInTheDocument();
    });
  });
});
