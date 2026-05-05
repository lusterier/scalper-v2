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

const sampleSignalDetail = {
  id: 100,
  received_at: "2026-05-05T10:00:00Z",
  schema_version: "1.0",
  source: "tv",
  idempotency_key: "abc-123",
  symbol: "BTCUSDT",
  original_symbol: null,
  action: "LONG",
  payload: {},
  ingestion_status: "validated" as const,
  correlation_id: "corr-12345678",
};

const sampleEvaluation = {
  id: 200,
  bot_id: "alpha",
  signal_id: 100,
  evaluated_at: "2026-05-05T10:00:01Z",
  trigger_threshold: 0.5,
  total_score: 0.7,
  decision: "execute" as const,
  config_version: 3,
  rule_results: [
    { name: "rsi_below_30", weight: 0.3, applied_weight: 0.3, result: "True", error: null },
  ],
  feature_snapshot: { "ind.btcusdt.15m.rsi_14": 42.5 },
  correlation_id: "corr-12345678",
};

describe("ScoringDrillDown route (T-418)", () => {
  it("parses signalId from URL and fetches both /api/signals/<id> + /api/scoring/by-signal/<id>", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/signals/100") return Promise.resolve(sampleSignalDetail);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [sampleEvaluation] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/scoring/100");
    await waitFor(() => {
      expect(screen.getByTestId("scoring-header")).toHaveTextContent("Signal #100");
    });
    const signalCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string) === "/api/signals/100",
    );
    const scoringCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string) === "/api/scoring/by-signal/100",
    );
    expect(signalCall).toBeDefined();
    expect(scoringCall).toBeDefined();
  });

  it("signal summary populated from query data", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/signals/100") return Promise.resolve(sampleSignalDetail);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [sampleEvaluation] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/scoring/100");
    await waitFor(() => {
      const summary = screen.getByTestId("signal-summary");
      expect(summary).toHaveTextContent("BTCUSDT");
      expect(summary).toHaveTextContent("LONG");
      expect(summary).toHaveTextContent("tv");
      expect(summary).toHaveTextContent("abc-123");
    });
  });

  it("ScoringBreakdownView populated with rule_results iteration", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/signals/100") return Promise.resolve(sampleSignalDetail);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [sampleEvaluation] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/scoring/100");
    await waitFor(() => {
      expect(screen.getByTestId("scoring-breakdown")).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("scoring-rule-row")).toHaveLength(1);
    expect(screen.getByText("rsi_below_30")).toBeInTheDocument();
  });

  it("FeatureSnapshotTable renders for each evaluation (per OQ-4=B)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/signals/100") return Promise.resolve(sampleSignalDetail);
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [sampleEvaluation] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/scoring/100");
    await waitFor(() => {
      expect(screen.getByTestId("feature-snapshot-table")).toBeInTheDocument();
    });
    expect(screen.getByTestId("feature-snapshot-row")).toBeInTheDocument();
    expect(screen.getByText("ind.btcusdt.15m.rsi_14")).toBeInTheDocument();
  });

  it("404 handling per T-414 WG#2 echo + WG#6 downstream gate (per WG#5/WG#6)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/signals/garbage") {
        return Promise.reject(
          new Error("API /api/signals/garbage failed: 404 not found"),
        );
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/scoring/garbage");
    await waitFor(() => {
      expect(screen.getByTestId("signal-not-found")).toHaveTextContent(
        "Signal #garbage not found",
      );
    });
    // Per WG#6 — scoring fetch MUST NOT fire when signal 404s.
    const scoringCall = mockFetch.mock.calls.find(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/scoring/"),
    );
    expect(scoringCall).toBeUndefined();
  });

  it("'No scoring evaluations' placeholder when evaluations=[] (200 with empty list per WG#5)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/signals/100") return Promise.resolve(sampleSignalDetail);
      // 200 with empty list — distinct from 404 entity-missing.
      if (url === "/api/scoring/by-signal/100")
        return Promise.resolve({ evaluations: [] });
      return Promise.reject(new Error("unmocked"));
    });
    mountAt("/scoring/100");
    await waitFor(() => {
      expect(screen.getByTestId("no-scoring-evaluations")).toHaveTextContent(
        /No scoring evaluations/,
      );
    });
    // FeatureSnapshotTable section absent when no evaluations.
    expect(screen.queryByTestId("feature-snapshot-table")).not.toBeInTheDocument();
  });
});
