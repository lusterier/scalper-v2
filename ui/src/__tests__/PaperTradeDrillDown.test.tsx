// T-516a2 — slim contract test for /paper-trades/$paperTradeId route.
// Mirror TradeDrillDown.test.tsx pattern; 6 tests covering header +
// summary + 404 fallback + null signal_id + signal+scoring queries +
// placeholder #4 wording.

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

const paperTradeWithSignal = {
  id: 42,
  bot_id: "alpha",
  signal_id: 200,
  open_order_id: 50,
  close_order_id: 51,
  symbol: "ETHUSDT",
  side: "long",
  entry_price: "3000.00",
  exit_price: "3100.00",
  qty: "0.5",
  notional_usd: "1500.00",
  realized_pnl: "50.00",
  fees_paid: "1.50",
  close_reason: "tp",
  opened_at: "2026-05-09T10:00:00Z",
  closed_at: "2026-05-09T12:00:00Z",
  status: "closed",
  mfe_pct: 0.034,
  mae_pct: -0.002,
  confidence_score: 0.81,
  meta: {},
};

const paperTradeWithoutSignal = { ...paperTradeWithSignal, id: 43, signal_id: null };

const sampleSignal = {
  id: 200,
  received_at: "2026-05-09T09:59:30Z",
  schema_version: "1.0",
  source: "tv",
  idempotency_key: "paper-abc",
  symbol: "ETHUSDT",
  original_symbol: null,
  action: "long_open",
  payload: {},
  ingestion_status: "validated",
  correlation_id: "corr-papertrade",
};

describe("PaperTradeDrillDown route (T-516a2)", () => {
  it("renders 'Paper trade #N' header from URL param", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/paper-trades/42") return Promise.resolve(paperTradeWithSignal);
      if (url === "/api/signals/200") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/200")
        return Promise.resolve({ evaluations: [] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/paper-trades/42");
    await waitFor(() => {
      expect(screen.getByText("Paper trade #42")).toBeInTheDocument();
    });
  });

  it("renders TradeSummary fields from PaperTrade response (shared module)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/paper-trades/42") return Promise.resolve(paperTradeWithSignal);
      if (url === "/api/signals/200") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/200")
        return Promise.resolve({ evaluations: [] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/paper-trades/42");
    await waitFor(() => {
      const summary = screen.getByTestId("trade-summary");
      // Sentinel fields verifying shared TradeSummary renders PaperTrade.
      expect(summary).toHaveTextContent("alpha");
      expect(summary).toHaveTextContent("ETHUSDT");
      expect(summary).toHaveTextContent("3000.00");
      expect(summary).toHaveTextContent("3100.00");
    });
  });

  it("renders 'Paper trade #N not found' on 404", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/paper-trades/99") {
        return Promise.reject(
          new Error("API /api/paper-trades/99 failed: 404 paper trade 99 not found"),
        );
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades/99");
    await waitFor(() => {
      expect(screen.getByTestId("trade-not-found")).toHaveTextContent(
        "Paper trade #99 not found",
      );
    });
    // L-017 active control — pin BOTH "what was called" AND "what was
    // NOT called" sides. Signals + scoring queries MUST NOT fire when
    // tradeQuery 404s (enabled gate per WG#7).
    const signalCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/signals/"),
    );
    const scoringCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/scoring/"),
    );
    expect(signalCall).toBeUndefined();
    expect(scoringCall).toBeUndefined();
  });

  it("null signal_id renders 'No signal' fallback + skips signal/scoring queries (WG#7)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/paper-trades/43")
        return Promise.resolve(paperTradeWithoutSignal);
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades/43");
    await waitFor(() => {
      expect(
        screen.getByText(/No signal \(manual or reconcile-driven trade\)/),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("No scoring evaluation")).toBeInTheDocument();
    // L-017 active control — strict not-called assertion.
    const signalCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/signals/"),
    );
    const scoringCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/scoring/"),
    );
    expect(signalCall).toBeUndefined();
    expect(scoringCall).toBeUndefined();
  });

  it("non-null signal_id fires /api/signals/<id> + /api/scoring/by-signal/<id> queries", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/paper-trades/42") return Promise.resolve(paperTradeWithSignal);
      if (url === "/api/signals/200") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/200")
        return Promise.resolve({ evaluations: [] });
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades/42");
    await waitFor(() => {
      expect(screen.getByTestId("signal-detail")).toBeInTheDocument();
    });
    const signalCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string) === "/api/signals/200",
    );
    const scoringCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string) === "/api/scoring/by-signal/200",
    );
    expect(signalCall).toBeDefined();
    expect(scoringCall).toBeDefined();
  });

  it("Shadow variants section renders ShadowVariantsView component (T-516b shipped) — placeholder count 5→4", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/paper-trades/42") return Promise.resolve(paperTradeWithSignal);
      if (url === "/api/signals/200") return Promise.resolve(sampleSignal);
      if (url === "/api/scoring/by-signal/200")
        return Promise.resolve({ evaluations: [] });
      if (url === "/api/paper-trades/42/shadow-variants")
        return Promise.resolve({ variants: [] });
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/paper-trades/42");
    await waitFor(() => {
      expect(screen.getByTestId("shadow-variants-view")).toBeInTheDocument();
    });
    // T-516b: Shadow variants now real component (NOT placeholder);
    // placeholder count drops 5 → 4 per AC#15a.
    const placeholders = screen.getAllByTestId("timeline-placeholder");
    expect(placeholders).toHaveLength(4);
    placeholders.forEach((p) => {
      expect(p.textContent).toMatch(/Coming F4\+|Coming F5\+/);
    });
  });
});
