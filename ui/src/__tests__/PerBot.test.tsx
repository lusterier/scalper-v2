import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "../routeTree.gen";
import { useSSEStore } from "../store/sse";

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

interface MockEventSource {
  url: string;
  close: ReturnType<typeof vi.fn>;
  onopen: (() => void) | null;
  onmessage: ((msg: { data: string }) => void) | null;
  onerror: (() => void) | null;
}
let lastInstance: MockEventSource | null = null;

class FakeEventSource implements MockEventSource {
  url: string;
  close = vi.fn(() => undefined);
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    const self: MockEventSource = this;
    lastInstance = self;
  }
}

beforeEach(() => {
  mockFetch.mockReset();
  lastInstance = null;
  useSSEStore.setState({ status: "unknown", lastEventAt: null, subscriberCount: 0 });
  // @ts-expect-error — override global EventSource
  globalThis.EventSource = FakeEventSource;
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
    </QueryClientProvider>,
  );
}

function defaultFetchImpl(botId: string) {
  return (url: string): Promise<unknown> => {
    if (url === "/api/bots/") {
      return Promise.resolve({
        bots: [
          {
            bot_id: botId,
            display_name: `Bot ${botId}`,
            created_at: "2026-05-04T00:00:00Z",
            status: "active",
            exchange_mode: "paper",
            config_hash: "x",
            config_applied_at: "2026-05-04T00:00:00Z",
            meta: {},
          },
        ],
      });
    }
    if (url.startsWith("/api/positions/")) {
      return Promise.resolve({ positions: [] });
    }
    if (url.startsWith("/api/signals/")) {
      return Promise.resolve({ signals: [], total: 0, limit: 50, offset: 0 });
    }
    if (url.startsWith("/api/analytics/pnl-series")) {
      return Promise.resolve({
        points: [],
        bot_id: botId,
        from_at: null,
        to_at: null,
        bucket: "hour",
      });
    }
    return Promise.reject(new Error(`unmocked URL: ${url}`));
  };
}

describe("PerBot route (T-413)", () => {
  it("uses botId param in /api/positions/?bot_id= query", async () => {
    mockFetch.mockImplementation(defaultFetchImpl("alpha"));
    mountAt("/bot/alpha");
    await waitFor(() => {
      const positionCalls = mockFetch.mock.calls.filter(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/positions/"),
      );
      expect(positionCalls.length).toBeGreaterThan(0);
      expect(positionCalls[0]?.[0]).toBe("/api/positions/?bot_id=alpha");
    });
  });

  it("renders 3 panels (positions, signals feed, P&L chart) when known botId", async () => {
    mockFetch.mockImplementation(defaultFetchImpl("alpha"));
    mountAt("/bot/alpha");
    await waitFor(() => {
      expect(screen.getByText("Open positions")).toBeInTheDocument();
    });
    expect(screen.getByText("Live signals")).toBeInTheDocument();
    expect(screen.getByText("Cumulative P&L (24h)")).toBeInTheDocument();
  });

  it("renders 'Bot not found' when botId is unknown (per WG#9)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl("alpha"));
    mountAt("/bot/garbage");
    await waitFor(() => {
      expect(screen.getByTestId("bot-not-found")).toHaveTextContent(
        'Bot "garbage" not found',
      );
    });
    expect(screen.getByText("Back to Overview")).toBeInTheDocument();
  });

  it("SSE event of type=signals prepends to feed (drop-oldest at 50)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl("alpha"));
    mountAt("/bot/alpha");
    await waitFor(() => {
      expect(screen.getByText("Open positions")).toBeInTheDocument();
    });
    // Simulate a server-side signals event arriving via EventSource.
    act(() => {
      lastInstance?.onmessage?.({
        data: JSON.stringify({
          type: "signals",
          payload: {
            id: 999,
            received_at: "2026-05-05T13:00:00Z",
            symbol: "BTCUSDT",
            action: "long_open",
            ingestion_status: "validated",
            correlation_id: "feed-test-1",
          },
          correlation_id: "feed-test-1",
          published_at: "2026-05-05T13:00:00+00:00",
        }),
      });
    });
    await waitFor(() => {
      expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    });
  });

  it("SSE event of type=positions invalidates positions query (refetch fired)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl("alpha"));
    mountAt("/bot/alpha");
    await waitFor(() => {
      expect(screen.getByText("Open positions")).toBeInTheDocument();
    });
    const callsBefore = mockFetch.mock.calls.filter(
      (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/positions/"),
    ).length;
    act(() => {
      lastInstance?.onmessage?.({
        data: JSON.stringify({
          type: "positions",
          payload: { event_type: "order_closed" },
          correlation_id: "evt-1",
          published_at: "2026-05-05T13:00:00+00:00",
        }),
      });
    });
    await waitFor(() => {
      const callsAfter = mockFetch.mock.calls.filter(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/positions/"),
      ).length;
      expect(callsAfter).toBeGreaterThan(callsBefore);
    });
  });

  it("ConnectionDot reflects useSSEStore status", async () => {
    mockFetch.mockImplementation(defaultFetchImpl("alpha"));
    mountAt("/bot/alpha");
    await waitFor(() => {
      expect(screen.getByText("Open positions")).toBeInTheDocument();
    });
    act(() => {
      useSSEStore.getState().setStatus("connected");
    });
    await waitFor(() => {
      const dot = screen.getByTestId("connection-dot");
      expect(dot.getAttribute("data-status")).toBe("connected");
    });
  });
});
