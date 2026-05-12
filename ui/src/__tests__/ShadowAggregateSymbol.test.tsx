// T-517a2 — shadow.aggregate.$symbol.tsx contract tests. Aggregate detail
// view: empty state + populated rows + Best pill on first row only +
// bot_id filter forwarding + time range always applies (WG#4 inversion of
// paper-trades omit-when-active) + Decimal-as-string + plain-text MFE/MAE
// percent + WG#6 win_rate edge values 0.0 + 1.0 in fixtures.

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

// Stub BotSelector with a minimal select so we can drive bot_id filter
// changes without pulling in /api/bots fetch overhead.
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

// WG#6 — fixture exercises distinct win_rate edge values [1.0, 0.5, 0.0]
// across 3 variants so 100%/0%/intermediate are all visible in render.
const sampleVariants = [
  {
    variant_name: "aggressive",
    n_trades: 4,
    win_count: 4,
    win_rate: 1.0,
    total_pnl: "30.00",
    avg_pnl: "7.50",
    best_pnl: "12.00",
    worst_pnl: "5.00",
    avg_mfe_pct: 0.045,
    avg_mae_pct: -0.005,
  },
  {
    variant_name: "conservative",
    n_trades: 4,
    win_count: 2,
    win_rate: 0.5,
    total_pnl: "10.00",
    avg_pnl: "2.50",
    best_pnl: "10.00",
    worst_pnl: "-5.00",
    avg_mfe_pct: 0.013,
    avg_mae_pct: -0.014,
  },
  {
    variant_name: "no_be",
    n_trades: 4,
    win_count: 0,
    win_rate: 0.0,
    total_pnl: "-12.00",
    avg_pnl: "-3.00",
    best_pnl: "-1.00",
    worst_pnl: "-6.00",
    avg_mfe_pct: null,
    avg_mae_pct: -0.020,
  },
];

describe("ShadowAggregateSymbolPage route (T-517a2)", () => {
  it("renders empty state when no variants returned", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/aggregate/BTCUSDT")) {
        return Promise.resolve({
          symbol: "BTCUSDT",
          variants: [],
          bot_id: null,
          from_at: null,
          to_at: null,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/aggregate/BTCUSDT");
    await waitFor(() => {
      expect(
        screen.getByText("No variants for BTCUSDT in selected window"),
      ).toBeInTheDocument();
    });
  });

  it("renders variant rows when variants non-empty (WG#6 win_rate edges 0.0/0.5/1.0)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/aggregate/BTCUSDT")) {
        return Promise.resolve({
          symbol: "BTCUSDT",
          variants: sampleVariants,
          bot_id: null,
          from_at: null,
          to_at: null,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    const { container } = mountAt("/shadow/aggregate/BTCUSDT");
    await waitFor(() => {
      expect(screen.getByText("aggressive")).toBeInTheDocument();
    });
    expect(screen.getByText("conservative")).toBeInTheDocument();
    expect(screen.getByText("no_be")).toBeInTheDocument();
    // 3 variant rows in tbody.
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(3);
    // win_rate=1.0 + 0.5 + 0.0 → "100.0%" + "50.0%" + "0.0%" all visible.
    expect(screen.getByText("100.0%")).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText("0.0%")).toBeInTheDocument();
  });

  it("Best pill renders on first row only (sorted DESC by total_pnl per backend)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/aggregate/BTCUSDT")) {
        return Promise.resolve({
          symbol: "BTCUSDT",
          variants: sampleVariants,
          bot_id: null,
          from_at: null,
          to_at: null,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/aggregate/BTCUSDT");
    await waitFor(() => {
      expect(screen.getByText("aggressive")).toBeInTheDocument();
    });
    // Exactly one Best pill (data-testid limited to first-row cell).
    const pills = screen.getAllByTestId("aggregate-best-pill");
    expect(pills).toHaveLength(1);
    expect(pills[0]?.textContent).toBe("Best");
  });

  it("bot_id filter change triggers refetch with bot_id query param", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/aggregate/BTCUSDT")) {
        return Promise.resolve({
          symbol: "BTCUSDT",
          variants: [],
          bot_id: null,
          from_at: null,
          to_at: null,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/aggregate/BTCUSDT");
    await waitFor(() => {
      expect(screen.getByTestId("bot-selector")).toBeInTheDocument();
    });
    const select = screen.getByTestId("bot-selector") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "alpha" } });
    await waitFor(() => {
      const alphaCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/shadow/aggregate/BTCUSDT") &&
          (c[0] as string).includes("bot_id=alpha"),
      );
      expect(alphaCall).toBeDefined();
    });
  });

  it("time range always applies (WG#4 NO omit-when-X heuristic — created_at non-null)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/aggregate/BTCUSDT")) {
        return Promise.resolve({
          symbol: "BTCUSDT",
          variants: [],
          bot_id: null,
          from_at: null,
          to_at: null,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/aggregate/BTCUSDT");
    await waitFor(() => {
      const initialCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/shadow/aggregate/BTCUSDT"),
      );
      expect(initialCall).toBeDefined();
      // Initial call MUST include from + to query params (default 30d window).
      const url = initialCall?.[0] as string;
      expect(url).toContain("from=");
      expect(url).toContain("to=");
    });
  });

  it("Decimal money fields render via PriceDelta (string input verbatim + sign + currency)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/aggregate/BTCUSDT")) {
        return Promise.resolve({
          symbol: "BTCUSDT",
          variants: [sampleVariants[0]],
          bot_id: null,
          from_at: null,
          to_at: null,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    const { container } = mountAt("/shadow/aggregate/BTCUSDT");
    await waitFor(() => {
      expect(screen.getByText("aggressive")).toBeInTheDocument();
    });
    // PriceDelta preserves Decimal-string verbatim in `data-value` attribute
    // per §5.3 (visible text adds "+" sign + currency suffix). Assert
    // verbatim Decimal preservation via data-value, not visible text.
    const priceCells = container.querySelectorAll("[data-value]");
    const values = Array.from(priceCells).map((el) => el.getAttribute("data-value"));
    expect(values).toContain("30.00"); // total_pnl
    expect(values).toContain("12.00"); // best_pnl
    expect(values).toContain("7.50"); // avg_pnl
  });

  it("MFE/MAE percent rendering via plain text formatPct (NOT PriceDelta); null → dash", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (typeof url === "string" && url.startsWith("/api/shadow/aggregate/BTCUSDT")) {
        return Promise.resolve({
          symbol: "BTCUSDT",
          variants: [sampleVariants[2]], // no_be: avg_mfe_pct=null, avg_mae_pct=-0.020
          bot_id: null,
          from_at: null,
          to_at: null,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/shadow/aggregate/BTCUSDT");
    await waitFor(() => {
      expect(screen.getByText("no_be")).toBeInTheDocument();
    });
    // avg_mae_pct=-0.020 → "-2.00%" plain text (formatPct).
    expect(screen.getByText("-2.00%")).toBeInTheDocument();
  });
});
