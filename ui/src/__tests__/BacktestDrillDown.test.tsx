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

const baseRun = {
  id: "1234abcd-5678-90ef-1234-567890abcdef",
  name: "baseline-2026",
  bot_id: "alpha",
  config_yaml: "bot_id: alpha\nexchange:\n  mode: paper\n",
  config_hash: "deadbeefcafebabe1234567890abcdef",
  date_range_start: "2026-01-01T00:00:00Z",
  date_range_end: "2026-02-01T00:00:00Z",
  status: "queued" as const,
  started_at: "2026-05-05T10:00:00Z",
  finished_at: null,
  summary: null,
  notes: null,
};

describe("BacktestDrillDown route (T-415)", () => {
  it("parses runId from URL and fetches /api/backtests/<runId>", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === `/api/backtests/${baseRun.id}`) return Promise.resolve(baseRun);
      return Promise.reject(new Error("unmocked"));
    });
    mountAt(`/backtests/${baseRun.id}`);
    await waitFor(() => {
      expect(screen.getByTestId("backtest-name")).toHaveTextContent("baseline-2026");
    });
  });

  it("metadata grid populated from query data", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === `/api/backtests/${baseRun.id}`) return Promise.resolve(baseRun);
      return Promise.reject(new Error("unmocked"));
    });
    mountAt(`/backtests/${baseRun.id}`);
    await waitFor(() => {
      const grid = screen.getByTestId("run-metadata");
      expect(grid).toHaveTextContent("alpha");
      expect(grid).toHaveTextContent("2026-01-01 00:00:00 UTC");
      expect(grid).toHaveTextContent("2026-02-01 00:00:00 UTC");
      expect(grid).toHaveTextContent("2026-05-05 10:00:00 UTC");
    });
  });

  it("StatusBadge renders correct tone for each status (queued/running/completed/failed)", async () => {
    const statuses: Array<"queued" | "running" | "completed" | "failed"> = [
      "queued",
      "running",
      "completed",
      "failed",
    ];
    for (const s of statuses) {
      mockFetch.mockReset();
      mockFetch.mockImplementation((url: string) => {
        if (url === `/api/backtests/${baseRun.id}`) {
          return Promise.resolve({ ...baseRun, status: s });
        }
        return Promise.reject(new Error("unmocked"));
      });
      const { unmount } = mountAt(`/backtests/${baseRun.id}`);
      await waitFor(() => {
        const badge = screen
          .getAllByText(s)
          .find((el) => el.getAttribute("data-kind") === "backtest");
        expect(badge).toBeDefined();
        expect(badge?.getAttribute("data-status")).toBe(s);
      });
      unmount();
    }
  });

  it("summary section renders 'F5+ worker pending' when summary=null (per WG#5 verbatim lock)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === `/api/backtests/${baseRun.id}`) return Promise.resolve(baseRun);
      return Promise.reject(new Error("unmocked"));
    });
    mountAt(`/backtests/${baseRun.id}`);
    await waitFor(() => {
      expect(screen.getByTestId("summary-pending")).toHaveTextContent(
        /F5\+ worker pending/i,
      );
    });
  });

  it("summary section renders JSON pretty-print when summary non-null", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === `/api/backtests/${baseRun.id}`) {
        return Promise.resolve({
          ...baseRun,
          status: "completed",
          summary: { total_trades: 42, win_rate: 0.55, expectancy: 3.14 },
          finished_at: "2026-05-05T11:00:00Z",
        });
      }
      return Promise.reject(new Error("unmocked"));
    });
    mountAt(`/backtests/${baseRun.id}`);
    await waitFor(() => {
      const pre = screen.getByTestId("summary-json");
      expect(pre.textContent).toContain("total_trades");
      expect(pre.textContent).toContain("42");
      expect(pre.textContent).toContain("expectancy");
    });
  });

  it("404 handling — error.message.includes('404') renders 'Backtest #X not found' + Back link", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === `/api/backtests/garbage-id`) {
        return Promise.reject(
          new Error("API /api/backtests/garbage-id failed: 404 backtest run garbage-id not found"),
        );
      }
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/backtests/garbage-id");
    await waitFor(() => {
      expect(screen.getByTestId("backtest-not-found")).toHaveTextContent(
        "Backtest #garbage-id not found",
      );
    });
    expect(screen.getByText("Back to Backtests")).toBeInTheDocument();
  });
});
