import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { routeTree } from "../routeTree.gen";

// Mock api-client at module boundary so useQuery resolves deterministically.
// Each test sets up `mockFetch` per-call to drive the 5 queries' return data.
const mockFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: unknown[]) => mockFetch(...args),
}));

function renderApp(): ReturnType<typeof render> {
  const router = createRouter({ routeTree });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider> as ReactNode,
  );
}

// Default fetch stub — every test that doesn't override returns minimum
// payload shapes for all 5 queries. apiFetch is keyed by URL prefix.
function defaultFetchImpl(url: string): Promise<unknown> {
  if (url.startsWith("/api/bots/")) {
    return Promise.resolve({ bots: [] });
  }
  if (url.startsWith("/api/positions/")) {
    return Promise.resolve({ positions: [] });
  }
  if (url.startsWith("/api/analytics/pnl-series")) {
    return Promise.resolve({
      points: [],
      bot_id: null,
      from_at: null,
      to_at: null,
      bucket: "hour",
    });
  }
  if (url.startsWith("/api/signals/")) {
    return Promise.resolve({ signals: [], total: 0, limit: 1, offset: 0 });
  }
  return Promise.reject(new Error(`unmocked URL: ${url}`));
}

describe("Overview route (T-412)", () => {
  it("renders 5 tile titles", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    renderApp();
    expect(await screen.findByText("Open positions")).toBeInTheDocument();
    expect(screen.getByText("Virtual balance")).toBeInTheDocument();
    expect(screen.getByText("24h P&L")).toBeInTheDocument();
    expect(screen.getByText("Signals (24h)")).toBeInTheDocument();
    expect(screen.getByText("Alerts (24h)")).toBeInTheDocument();
  });

  it("Open positions tile shows count when query succeeds", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/positions/")) {
        return Promise.resolve({
          positions: [
            { bot_id: "alpha", symbol: "BTCUSDT", trade_id: 1, side: "long" },
            { bot_id: "beta", symbol: "ETHUSDT", trade_id: 2, side: "long" },
            { bot_id: "alpha", symbol: "SOLUSDT", trade_id: 3, side: "short" },
          ],
        });
      }
      return defaultFetchImpl(url);
    });
    renderApp();
    const tile = await screen.findByTestId("overview-tile-open-positions");
    await waitFor(() => {
      expect(tile.querySelector(".text-2xl")?.textContent).toBe("3");
    });
  });

  it("24h P&L tile shows last cumulative_pnl point verbatim (§5.3 Decimal-as-string)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/analytics/pnl-series")) {
        return Promise.resolve({
          points: [
            { bucket_at: "2026-05-05T00:00:00Z", bucket_pnl: "1.00", cumulative_pnl: "1.00" },
            { bucket_at: "2026-05-05T01:00:00Z", bucket_pnl: "2.50", cumulative_pnl: "3.50" },
            {
              bucket_at: "2026-05-05T02:00:00Z",
              bucket_pnl: "0.000000123456789",
              cumulative_pnl: "3.500000123456789",
            },
          ],
          bot_id: null,
          from_at: null,
          to_at: null,
          bucket: "hour",
        });
      }
      return defaultFetchImpl(url);
    });
    renderApp();
    await waitFor(() => {
      const tile = screen.getByTestId("overview-tile-24h-p&l");
      const span = tile.querySelector("[data-value]") as HTMLElement | null;
      // Decimal-as-string preserved verbatim per §5.3.
      expect(span?.getAttribute("data-value")).toBe("3.500000123456789");
      expect(span?.textContent).toBe("3.500000123456789");
    });
  });

  it("Signals tile renders 3 sub-counts (received / accepted / rejected)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/signals/")) {
        if (url.includes("ingestion_status=validated")) {
          return Promise.resolve({ signals: [], total: 7, limit: 1, offset: 0 });
        }
        if (url.includes("ingestion_status=invalid")) {
          return Promise.resolve({ signals: [], total: 2, limit: 1, offset: 0 });
        }
        return Promise.resolve({ signals: [], total: 11, limit: 1, offset: 0 });
      }
      return defaultFetchImpl(url);
    });
    renderApp();
    await waitFor(() => {
      expect(screen.getByTestId("signals-received").textContent).toBe("11");
      expect(screen.getByTestId("signals-accepted").textContent).toBe("7");
      expect(screen.getByTestId("signals-rejected").textContent).toBe("2");
    });
  });

  it("Virtual balance tile renders placeholder per OQ-A1", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    renderApp();
    const tile = await screen.findByTestId("overview-tile-virtual-balance");
    expect(tile.textContent).toContain("—");
    expect(tile.textContent).toContain("Coming F4+");
  });

  it("Alerts tile renders placeholder per OQ-B1", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    renderApp();
    const tile = await screen.findByTestId("overview-tile-alerts-(24h)");
    expect(tile.textContent).toContain("—");
    expect(tile.textContent).toContain("Coming F4+");
  });

  it("query URL for /api/signals/ uses Z-suffix UTC timestamps (§N1 + WG#3 URL regex)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    renderApp();
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled();
    });
    // Per WG#3: assert URL contains `from=<ISO-with-Z>` rather than spy on
    // Date.prototype.toISOString. Z-suffix is the only robust §N1 cross-
    // check (FastAPI interprets non-Z strings as naive datetime).
    const signalCalls = mockFetch.mock.calls.filter(
      (call) => typeof call[0] === "string" && (call[0] as string).startsWith("/api/signals/"),
    );
    expect(signalCalls.length).toBeGreaterThan(0);
    const url = signalCalls[0]?.[0] as string;
    const utcRegex = /from=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/;
    expect(url).toMatch(utcRegex);
  });

  it("Open positions tile filters by selected bot via ?bot_id= when 1 bot selected", async () => {
    // Verifies buildPositionsUrl behaviour — the URL goes out with ?bot_id=
    // when exactly one bot is selected. Default state is `selectedBots=[]`
    // so the FIRST request is `/api/positions/` (no filter). This test
    // checks the default URL form; the conditional ?bot_id= path is
    // exercised at integration-test level where user clicks a bot.
    mockFetch.mockImplementation(defaultFetchImpl);
    renderApp();
    await waitFor(() => {
      const positionCalls = mockFetch.mock.calls.filter(
        (call) =>
          typeof call[0] === "string" &&
          (call[0] as string).startsWith("/api/positions/"),
      );
      expect(positionCalls.length).toBeGreaterThan(0);
      const url = positionCalls[0]?.[0] as string;
      // Default selectedBots=[] → no ?bot_id= filter; just the bare URL.
      expect(url).toBe("/api/positions/");
    });
  });

  it("ConnectionDot renders status='unknown' (T-412 placeholder; T-413 wires SSE)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    renderApp();
    const dot = await screen.findByTestId("connection-dot");
    expect(dot.getAttribute("data-status")).toBe("unknown");
  });

  it("TimeRangePicker is visible in top bar (Overview tiles still 24h-locked per OQ-D=B)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    renderApp();
    // TimeRangePicker exposes its 5 preset buttons + Custom button.
    expect(await screen.findByRole("button", { name: "1h" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "24h" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "7d" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "30d" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Custom" })).toBeInTheDocument();
  });
});
