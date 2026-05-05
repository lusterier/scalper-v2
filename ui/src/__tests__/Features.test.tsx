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

vi.mock("recharts", async (importActual) => {
  const actual = (await importActual()) as Record<string, unknown>;
  const FixedSizeContainer = ({ children }: { children: React.ReactNode }): React.JSX.Element => (
    <div style={{ width: 600, height: 240 }}>{children}</div>
  );
  return { ...actual, ResponsiveContainer: FixedSizeContainer };
});

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

const numericFeature = {
  feature_name: "ind.btcusdt.15m.ema_20",
  symbol: "BTCUSDT",
  computed_at: new Date().toISOString(), // fresh
  value_num: 50_000.123_456,
  value_bool: null,
  value_json: null,
  source_version: "1.0",
};

const boolFeature = {
  feature_name: "ind.btcusdt.1m.is_above_ma",
  symbol: "BTCUSDT",
  computed_at: new Date().toISOString(),
  value_num: null,
  value_bool: true,
  value_json: null,
  source_version: "1.0",
};

describe("Features route (T-417)", () => {
  it("renders DataTable with features from /api/features/latest", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({
          features: [numericFeature, boolFeature],
          total: 2,
          limit: 100,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    await waitFor(() => {
      expect(screen.getByText("ind.btcusdt.15m.ema_20")).toBeInTheDocument();
      expect(screen.getByText("ind.btcusdt.1m.is_above_ma")).toBeInTheDocument();
    });
  });

  it("prefix filter appends ?prefix= URL param (empty omits per WG#3)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({ features: [], total: 0, limit: 100, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    // Initial mount with empty prefix — URL must NOT contain ?prefix=.
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/features/latest"),
      );
      expect(call).toBeDefined();
      const url = call?.[0] as string;
      expect(url).not.toMatch(/[?&]prefix=/);
    });
    // Type prefix → URL contains ?prefix=ind.btc.
    fireEvent.change(screen.getByTestId("prefix-input"), {
      target: { value: "ind.btc" },
    });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).includes("prefix=ind.btc"),
      );
      expect(call).toBeDefined();
    });
  });

  it("history fetch is gated on row selection per WG#4 (NOT fired on initial mount)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({
          features: [numericFeature],
          total: 1,
          limit: 100,
          offset: 0,
        });
      }
      if (url.startsWith("/api/features/history")) {
        return Promise.resolve({ features: [], total: 0, limit: 1000, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    await waitFor(() => {
      expect(screen.getByText("ind.btcusdt.15m.ema_20")).toBeInTheDocument();
    });
    // Per WG#4 — no history calls before row selection.
    const historyCallsBefore = mockFetch.mock.calls.filter(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/features/history"),
    );
    expect(historyCallsBefore).toHaveLength(0);
  });

  it("row click selects feature and triggers history fetch (numeric)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({
          features: [numericFeature],
          total: 1,
          limit: 100,
          offset: 0,
        });
      }
      if (url.startsWith("/api/features/history")) {
        return Promise.resolve({
          features: [numericFeature],
          total: 1,
          limit: 1000,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    await waitFor(() => {
      expect(screen.getByText("ind.btcusdt.15m.ema_20")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("ind.btcusdt.15m.ema_20"));
    await waitFor(() => {
      expect(screen.getByTestId("selected-feature-panel")).toBeInTheDocument();
      const historyCalls = mockFetch.mock.calls.filter(
        (c) =>
          typeof c[0] === "string" && (c[0] as string).startsWith("/api/features/history"),
      );
      expect(historyCalls.length).toBeGreaterThan(0);
    });
  });

  it("non-numeric value_bool feature renders 'Not chartable' placeholder + history list", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({
          features: [boolFeature],
          total: 1,
          limit: 100,
          offset: 0,
        });
      }
      if (url.startsWith("/api/features/history")) {
        return Promise.resolve({
          features: [boolFeature],
          total: 1,
          limit: 1000,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    await waitFor(() => {
      expect(screen.getByText("ind.btcusdt.1m.is_above_ma")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("ind.btcusdt.1m.is_above_ma"));
    await waitFor(() => {
      expect(screen.getByTestId("non-numeric-history")).toBeInTheDocument();
      expect(screen.getByText(/Not chartable/)).toBeInTheDocument();
    });
  });

  it("history fetch URL uses .toISOString() Z-suffix per §N1 (per WG#5)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({
          features: [numericFeature],
          total: 1,
          limit: 100,
          offset: 0,
        });
      }
      if (url.startsWith("/api/features/history")) {
        return Promise.resolve({ features: [], total: 0, limit: 1000, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    await waitFor(() => {
      expect(screen.getByText("ind.btcusdt.15m.ema_20")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("ind.btcusdt.15m.ema_20"));
    await waitFor(() => {
      const historyCall = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/features/history"),
      );
      expect(historyCall).toBeDefined();
      const url = historyCall?.[0] as string;
      expect(url).toMatch(/from=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
      expect(url).toMatch(/to=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
    });
  });

  it("renderValue handles value_num=0 as valid numeric (per WG#2 — !== null not falsy)", async () => {
    const zeroFeature = {
      feature_name: "ind.btcusdt.15m.macd_hist",
      symbol: "BTCUSDT",
      computed_at: new Date().toISOString(),
      value_num: 0,
      value_bool: null,
      value_json: null,
      source_version: "1.0",
    };
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({
          features: [zeroFeature],
          total: 1,
          limit: 100,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    await waitFor(() => {
      // value_num=0 must render "0.0000" verbatim, NOT placeholder "—".
      expect(screen.getByText("0.0000")).toBeInTheDocument();
      expect(screen.queryByText("—")).not.toBeInTheDocument();
    });
  });

  it("renderValue handles value_bool=false as valid bool (per WG#2 — !== null not falsy)", async () => {
    const falseFeature = {
      feature_name: "ind.btcusdt.1m.is_above_ma",
      symbol: "BTCUSDT",
      computed_at: new Date().toISOString(),
      value_num: null,
      value_bool: false,
      value_json: null,
      source_version: "1.0",
    };
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({
          features: [falseFeature],
          total: 1,
          limit: 100,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    await waitFor(() => {
      // value_bool=false must render "false" verbatim, NOT placeholder.
      expect(screen.getByText("false")).toBeInTheDocument();
    });
  });

  it("StalenessDot in browser row reflects fresh status for recent computed_at", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/features/latest")) {
        return Promise.resolve({
          features: [numericFeature],
          total: 1,
          limit: 100,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/features");
    await waitFor(() => {
      const dot = screen.getByTestId("staleness-dot");
      expect(dot.getAttribute("data-status")).toBe("fresh");
    });
  });
});
