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

const tradeWithSignal = {
  id: 7,
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

const tradeWithoutSignal = { ...tradeWithSignal, id: 8, signal_id: null };

const sampleSignal = {
  id: 100,
  received_at: "2026-05-05T09:59:30Z",
  schema_version: "1.0",
  source: "tv",
  idempotency_key: "abc-123",
  symbol: "BTCUSDT",
  original_symbol: null,
  action: "long_open",
  payload: {},
  ingestion_status: "validated",
  correlation_id: "corr-12345678",
};

const sampleEvaluation = {
  id: 200,
  bot_id: "alpha",
  signal_id: 100,
  evaluated_at: "2026-05-05T09:59:31Z",
  trigger_threshold: 0.5,
  total_score: 0.7,
  decision: "execute",
  config_version: 3,
  rule_results: [
    { name: "rsi_below_30", weight: 0.3, applied_weight: 0.3, result: "True", error: null },
    {
      name: "ema_cross",
      weight: 0.4,
      applied_weight: 0.4,
      result: "False",
      error: null,
    },
    {
      name: "fund_rate_check",
      weight: 0.3,
      applied_weight: 0.0,
      result: "data_missing",
      error: { error: "field 'funding_rate' not in feature snapshot" },
    },
  ],
  feature_snapshot: {},
  correlation_id: "corr-12345678",
};

describe("TradeDrillDown route (T-414)", () => {
  it("parses tradeId from URL and fetches /api/trades/<id>", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/7") return Promise.resolve(tradeWithSignal);
      if (url === `/api/signals/100`) return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [sampleEvaluation] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/trades/7");
    await waitFor(() => {
      expect(screen.getByText("Trade #7")).toBeInTheDocument();
    });
  });

  it("Trade summary section populated incl. close info (BRIEF 'close' tier folded into summary)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/7") return Promise.resolve(tradeWithSignal);
      if (url === "/api/signals/100") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/trades/7");
    await waitFor(() => {
      const summary = screen.getByTestId("trade-summary");
      // Summary includes close info per BRIEF "close" tier folded.
      expect(summary).toHaveTextContent("tp");
      expect(summary).toHaveTextContent("100.00");
      expect(summary).toHaveTextContent("51000.00");
      expect(summary).toHaveTextContent("2026-05-05 12:00:00 UTC");
    });
  });

  it("Signal section renders 'No signal' when signal_id is null", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/8") return Promise.resolve(tradeWithoutSignal);
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/trades/8");
    await waitFor(() => {
      expect(screen.getByText(/No signal \(manual or reconcile-driven trade\)/)).toBeInTheDocument();
    });
    expect(screen.getByText("No scoring evaluation")).toBeInTheDocument();
  });

  it("Signal section fetches /api/signals/<signal_id> when signal_id != null", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/7") return Promise.resolve(tradeWithSignal);
      if (url === "/api/signals/100") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [sampleEvaluation] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/trades/7");
    await waitFor(() => {
      const detail = screen.getByTestId("signal-detail");
      expect(detail).toHaveTextContent("BTCUSDT");
      expect(detail).toHaveTextContent("long_open");
      expect(detail).toHaveTextContent("tv");
      expect(detail).toHaveTextContent("abc-123");
    });
  });

  it("Scoring section renders rule_results iteration with loose result string per BLOCKER #1", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/7") return Promise.resolve(tradeWithSignal);
      if (url === "/api/signals/100") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [sampleEvaluation] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/trades/7");
    await waitFor(() => {
      expect(screen.getByTestId("scoring-breakdown")).toBeInTheDocument();
    });
    const rows = screen.getAllByTestId("scoring-rule-row");
    expect(rows).toHaveLength(3);
    // Verify the loose `result` string is rendered verbatim per BLOCKER #1.
    expect(rows[0]).toHaveTextContent("rsi_below_30");
    expect(rows[0]?.querySelector("[data-result]")?.getAttribute("data-result")).toBe("True");
    expect(rows[1]?.querySelector("[data-result]")?.getAttribute("data-result")).toBe("False");
    expect(rows[2]?.querySelector("[data-result]")?.getAttribute("data-result")).toBe(
      "data_missing",
    );
  });

  it("5 placeholder sections (Tier 3-7) render with Coming subtitle (T-516a2 placeholder #4 wording updated)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/7") return Promise.resolve(tradeWithSignal);
      if (url === "/api/signals/100") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/trades/7");
    await waitFor(() => {
      expect(screen.getByText(/Order events/)).toBeInTheDocument();
    });
    const placeholders = screen.getAllByTestId("timeline-placeholder");
    expect(placeholders).toHaveLength(5);
    // Per T-516a2 plan-reviewer WG#1: split forEach over 4 F4+/F5+
    // placeholders + 1 separate assertion on placeholder #4 (Shadow
    // variants) which now reads "Coming T-516b (... parent_kind=live)".
    // Pins exact text + preserves count==5 contract.
    const nonShadow = placeholders.filter(
      (p) => !p.textContent?.includes("Coming T-516b"),
    );
    expect(nonShadow).toHaveLength(4);
    nonShadow.forEach((p) => {
      expect(p.textContent).toMatch(/Coming F4\+|Coming F5\+/);
    });
    const shadow = placeholders.find((p) =>
      p.textContent?.includes("Coming T-516b"),
    );
    expect(shadow).toBeDefined();
    expect(shadow?.textContent).toContain(
      "Coming T-516b (shadow variants section per ADR-0010 parent_kind=live)",
    );
  });

  it("404 handling per WG#2 + WG#6 — renders 'Trade #X not found' + downstream queries NOT fired", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/garbage") {
        return Promise.reject(new Error("API /api/trades/garbage failed: 404 trade garbage not found"));
      }
      return Promise.reject(new Error(`unmocked URL: ${url}`));
    });
    mountAt("/trades/garbage");
    await waitFor(() => {
      expect(screen.getByTestId("trade-not-found")).toHaveTextContent(
        "Trade #garbage not found",
      );
    });
    // Per WG#6 — signals/scoring queries MUST NOT fire when trade query 404s.
    const signalCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/signals/"),
    );
    const scoringCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/scoring/"),
    );
    expect(signalCall).toBeUndefined();
    expect(scoringCall).toBeUndefined();
  });

  it("Back-to-Trades link rendered (drill-down + 404 branches)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/trades/7") return Promise.resolve(tradeWithSignal);
      if (url === "/api/signals/100") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/trades/7");
    await waitFor(() => {
      expect(screen.getByText("Back to Trades")).toBeInTheDocument();
    });
  });
});
