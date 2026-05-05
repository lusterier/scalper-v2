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

const sampleBot = {
  bot_id: "alpha",
  display_name: "Alpha Bot",
  created_at: "2026-05-04T00:00:00Z",
  status: "active" as const,
  exchange_mode: "paper" as const,
  config_hash: "deadbeefcafebabe1234567890",
  config_applied_at: "2026-05-05T10:00:00Z",
  meta: {},
};

const sampleEntry = {
  input_symbol: "BTCUSDT.P",
  canonical_symbol: "BTCUSDT",
  exchange_source: "binance" as const,
  notes: "TradingView alias",
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-05T10:00:00Z",
};

function defaultFetchImpl(url: string): Promise<unknown> {
  if (url === "/api/bots/") {
    return Promise.resolve({ bots: [sampleBot] });
  }
  if (url === "/api/symbol-map/") {
    return Promise.resolve({ entries: [sampleEntry] });
  }
  return Promise.reject(new Error(`unmocked: ${url}`));
}

describe("Settings route (T-420)", () => {
  it("Settings route mounts at /settings", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByTestId("bot-registry-section")).toBeInTheDocument();
    });
  });

  it("renders 4 sections in order: Bot registry + Symbol map + Plugin registry + API key status", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByTestId("bot-registry-section")).toBeInTheDocument();
    });
    expect(screen.getByTestId("symbol-map-section")).toBeInTheDocument();
    expect(screen.getByTestId("plugin-registry-placeholder")).toBeInTheDocument();
    expect(screen.getByTestId("api-key-status-placeholder")).toBeInTheDocument();
  });

  it("bot registry populated from /api/bots/", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByText("alpha")).toBeInTheDocument();
      expect(screen.getByText("Alpha Bot")).toBeInTheDocument();
    });
  });

  it("symbol map populated from /api/symbol-map/", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByText("BTCUSDT.P")).toBeInTheDocument();
      expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    });
  });
});
