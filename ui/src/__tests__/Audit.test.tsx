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

const eventA = {
  id: 100,
  occurred_at: "2026-05-05T10:00:00Z",
  actor: "lan:127.0.0.1",
  action: "bot_config.apply",
  entity_type: "bot_config",
  entity_id: "alpha",
  before_state: { version: 2 },
  after_state: { version: 3 },
  correlation_id: "corr-AAAA",
  meta: {},
};

const eventB = {
  id: 101,
  occurred_at: "2026-05-05T11:00:00Z",
  actor: "lan:127.0.0.1",
  action: "symbol_map.create",
  entity_type: "symbol_map",
  entity_id: "BTCUSDT",
  before_state: null,
  after_state: { exchange_symbol: "BTCUSDT", source: "tv" },
  correlation_id: "corr-BBBB",
  meta: {},
};

describe("Audit log route (T-419)", () => {
  it("renders DataTable with events from /api/audit/", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({ events: [eventA, eventB], total: 2, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit");
    await waitFor(() => {
      expect(screen.getByText("bot_config.apply")).toBeInTheDocument();
      expect(screen.getByText("symbol_map.create")).toBeInTheDocument();
    });
  });

  it("actor filter appends ?actor= URL param (empty omits)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({ events: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/audit/?"),
      );
      expect(call).toBeDefined();
      expect(call?.[0] as string).not.toMatch(/[?&]actor=/);
    });
    fireEvent.change(screen.getByTestId("actor-input"), {
      target: { value: "lan:127.0.0.1" },
    });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).includes("actor=lan%3A127.0.0.1"),
      );
      expect(call).toBeDefined();
    });
  });

  it("action_prefix filter appends ?action_prefix= URL param", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({ events: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit");
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("action-prefix-input"), {
      target: { value: "bot_config." },
    });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" && (c[0] as string).includes("action_prefix=bot_config."),
      );
      expect(call).toBeDefined();
    });
  });

  it("entity_type filter appends ?entity_type= URL param", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({ events: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit");
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("entity-type-input"), {
      target: { value: "bot_config" },
    });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" && (c[0] as string).includes("entity_type=bot_config"),
      );
      expect(call).toBeDefined();
    });
  });

  it("time-range filter uses .toISOString() Z-suffix per §N1", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({ events: [], total: 0, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/audit/?"),
      );
      expect(call).toBeDefined();
      const url = call?.[0] as string;
      expect(url).toMatch(/from=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
      expect(url).toMatch(/to=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
    });
  });

  it("?correlation_id= URL search param applies client-side filter + notice rendered (per WG#6 disambiguation)", async () => {
    // Test #6 — fixture has BOTH matching + non-matching correlation_ids;
    // after client-side filter, DataTable shows only matching event.
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({
          events: [eventA, eventB],
          total: 2,
          limit: 50,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit?correlation_id=corr-AAAA");
    await waitFor(() => {
      expect(screen.getByTestId("correlation-filter-notice")).toHaveTextContent(
        /corr-AAAA/,
      );
    });
    // eventA matches, eventB does NOT — only eventA visible after client filter.
    expect(screen.getByText("bot_config.apply")).toBeInTheDocument();
    expect(screen.queryByText("symbol_map.create")).not.toBeInTheDocument();
  });

  it("?correlation_id= URL search param does NOT add correlation_id to backend fetch URL (per WG#2 + WG#6)", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({ events: [eventA], total: 1, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit?correlation_id=corr-AAAA");
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalled();
    });
    const fetchCalls = mockFetch.mock.calls.filter(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/audit/?"),
    );
    expect(fetchCalls.length).toBeGreaterThan(0);
    fetchCalls.forEach((call) => {
      const url = call[0] as string;
      // Per WG#2 — backend lacks correlation_id filter; client-side
      // filter is the only path. URL must NOT contain the param.
      expect(url).not.toMatch(/[?&]correlation_id=/);
    });
  });

  it("custom Previous/Next pagination updates offset query param", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({
          events: Array.from({ length: 50 }, (_, i) => ({ ...eventA, id: 1000 + i })),
          total: 120,
          limit: 50,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit");
    await waitFor(() => {
      expect(screen.getByTestId("audit-pagination")).toBeInTheDocument();
    });
    const nextBtn = screen.getByRole("button", { name: "Next" });
    fireEvent.click(nextBtn);
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).includes("offset=50"),
      );
      expect(call).toBeDefined();
    });
  });
});
