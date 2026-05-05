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

const sampleSignal = {
  id: 100,
  received_at: "2026-05-05T10:00:00Z",
  symbol: "BTCUSDT",
  action: "LONG",
  ingestion_status: "validated" as const,
  correlation_id: "corr-12345678",
};

function defaultFetchImpl(url: string): Promise<unknown> {
  if (url.startsWith("/api/signals/?")) {
    return Promise.resolve({
      signals: [sampleSignal],
      total: 1,
      limit: 50,
      offset: 0,
    });
  }
  return Promise.reject(new Error(`unmocked: ${url}`));
}

describe("Scoring inspector list (T-418)", () => {
  it("renders DataTable with signals from /api/signals/", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/scoring");
    await waitFor(() => {
      expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
    });
  });

  it("source filter appends ?source= URL param", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/scoring");
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("source-input"), { target: { value: "tv" } });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).includes("source=tv"),
      );
      expect(call).toBeDefined();
    });
  });

  it("symbol filter appends ?symbol= URL param", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/scoring");
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("symbol-input"), { target: { value: "BTCUSDT" } });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).includes("symbol=BTCUSDT"),
      );
      expect(call).toBeDefined();
    });
  });

  it("action='all' OMITS ?action= URL param (per WG#4 negative regex)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/scoring");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/signals/?"),
      );
      expect(call).toBeDefined();
      const url = call?.[0] as string;
      expect(url).not.toMatch(/[?&]action=/);
    });
  });

  it("action='LONG' filter appends ?action=LONG (per WG#3 backend StrEnum value)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/scoring");
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId("action-filter"), { target: { value: "LONG" } });
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).includes("action=LONG"),
      );
      expect(call).toBeDefined();
    });
  });

  it("ingestion_status='all' OMITS ?ingestion_status= URL param (per WG#4)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/scoring");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/signals/?"),
      );
      expect(call).toBeDefined();
      const url = call?.[0] as string;
      expect(url).not.toMatch(/[?&]ingestion_status=/);
    });
  });

  it("time-range filter uses .toISOString() Z-suffix per §N1 (per WG#8)", async () => {
    mockFetch.mockImplementation(defaultFetchImpl);
    mountAt("/scoring");
    await waitFor(() => {
      const call = mockFetch.mock.calls.find(
        (c) => typeof c[0] === "string" && (c[0] as string).startsWith("/api/signals/?"),
      );
      expect(call).toBeDefined();
      const url = call?.[0] as string;
      expect(url).toMatch(/from=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
      expect(url).toMatch(/to=\d{4}-\d{2}-\d{2}T\d{2}%3A\d{2}%3A\d{2}(\.\d+)?Z/);
    });
  });
});
