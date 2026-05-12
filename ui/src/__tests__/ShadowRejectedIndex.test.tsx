// T-517b2 — shadow.rejected.tsx contract tests. Mirror PaperTradesIndex
// patterns: empty state + populated rows + pagination Next + bot filter
// refetch + status filter + NEW terminal_outcome filter + NEW time-range
// always-applies (NO omit; differs from paper-trades) + sanity for null
// terminal-fields rendering as dash.

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

const sampleTerminated = {
  id: 1,
  signal_id: 100,
  bot_id: "alpha",
  symbol: "BTCUSDT",
  would_side: "buy",
  created_at: "2026-05-05T10:00:00Z",
  terminated_at: "2026-05-05T11:00:00Z",
  terminal_outcome: "would_tp" as const,
  mfe_pct: 0.025,
  mae_pct: -0.005,
  meta: {},
};

const sampleActive = {
  id: 2,
  signal_id: 101,
  bot_id: "alpha",
  symbol: "ETHUSDT",
  would_side: "sell",
  created_at: "2026-05-05T10:30:00Z",
  terminated_at: null,
  terminal_outcome: null,
  mfe_pct: null,
  mae_pct: null,
  meta: {},
};

describe("ShadowRejectedPage route (T-517b2)", () => {
  it("renders empty state when no rejected signals match filters", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/rejected/")) {
        return Promise.resolve({ rejected: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/rejected");
    await waitFor(() => {
      expect(screen.getByText("No rejected signals match filters")).toBeInTheDocument();
    });
  });

  it("renders rows when rejected non-empty", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/rejected/")) {
        return Promise.resolve({
          rejected: [sampleTerminated, sampleActive],
          total: 2,
          limit: 50,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    const { container } = mountAt("/shadow/rejected");
    await waitFor(() => {
      expect(screen.getByTestId("shadow-rejected-pagination")).toHaveTextContent(
        "(2 rejected signals)",
      );
    });
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    // Symbol cell text visible.
    expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
  });

  it("Next button increments offset (page advance) — pagination URL", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/rejected/")) {
        return Promise.resolve({
          rejected: [sampleTerminated],
          total: 120,
          limit: 50,
          offset: url.includes("offset=50") ? 50 : 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/rejected");
    await waitFor(() => {
      expect(screen.getByText("Next")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("Next"));
    await waitFor(() => {
      const nextCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/shadow/rejected/") &&
          (c[0] as string).includes("offset=50"),
      );
      expect(nextCall).toBeDefined();
    });
  });

  it("bot filter change triggers refetch with bot_id query param + offset reset", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/rejected/")) {
        return Promise.resolve({ rejected: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/rejected");
    await waitFor(() => {
      expect(screen.getByTestId("bot-selector")).toBeInTheDocument();
    });
    const select = screen.getByTestId("bot-selector") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "alpha" } });
    await waitFor(() => {
      const alphaCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/shadow/rejected/") &&
          (c[0] as string).includes("bot_id=alpha"),
      );
      expect(alphaCall).toBeDefined();
      // Offset MUST reset to 0 on filter change.
      expect((alphaCall?.[0] as string).includes("offset=0")).toBe(true);
    });
  });

  it("status filter forwards 'active' query param", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/rejected/")) {
        return Promise.resolve({ rejected: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/rejected");
    await waitFor(() => {
      expect(screen.getByTestId("status-filter")).toBeInTheDocument();
    });
    const select = screen.getByTestId("status-filter") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "active" } });
    await waitFor(() => {
      const activeCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/shadow/rejected/") &&
          (c[0] as string).includes("status=active"),
      );
      expect(activeCall).toBeDefined();
    });
  });

  it("terminal_outcome filter forwards 'would_tp' query param", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/rejected/")) {
        return Promise.resolve({ rejected: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/rejected");
    await waitFor(() => {
      expect(screen.getByTestId("terminal-outcome-filter")).toBeInTheDocument();
    });
    const select = screen.getByTestId("terminal-outcome-filter") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "would_tp" } });
    await waitFor(() => {
      const tpCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/shadow/rejected/") &&
          (c[0] as string).includes("terminal_outcome=would_tp"),
      );
      expect(tpCall).toBeDefined();
    });
  });

  it("time range always applies regardless of status (NO omit-when-active heuristic)", async () => {
    // WG#4 — INVERSION of paper-trades.index pattern. shadow_rejected.created_at
    // is non-null per migration 0014, so ?from + ?to always meaningful even
    // when status=active.
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/rejected/")) {
        return Promise.resolve({ rejected: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/rejected");
    await waitFor(() => {
      expect(screen.getByTestId("status-filter")).toBeInTheDocument();
    });
    // Switch to status=active and ensure ?from + ?to STILL present.
    const select = screen.getByTestId("status-filter") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "active" } });
    await waitFor(() => {
      const activeCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/shadow/rejected/") &&
          (c[0] as string).includes("status=active"),
      );
      expect(activeCall).toBeDefined();
      const url = activeCall?.[0] as string;
      expect(url).toContain("from=");
      expect(url).toContain("to=");
    });
  });

  it("active row renders dash for null terminal_outcome / mfe_pct / mae_pct", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/rejected/")) {
        return Promise.resolve({
          rejected: [sampleActive],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    const { container } = mountAt("/shadow/rejected");
    await waitFor(() => {
      expect(screen.getByText("ETHUSDT")).toBeInTheDocument();
    });
    // 3 dashes expected in the active row (terminal_outcome + mfe + mae cells).
    const dashes = container.querySelectorAll("tbody td .text-muted-foreground");
    // At least 3 dash spans visible (terminal_outcome + mfe_pct + mae_pct).
    // created_at cell also has muted text, so count is ≥4. Just assert ≥3.
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });
});
