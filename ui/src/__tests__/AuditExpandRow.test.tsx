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

const eventBoth = {
  id: 100,
  occurred_at: "2026-05-05T10:00:00Z",
  actor: "lan:127.0.0.1",
  action: "bot_config.apply",
  entity_type: "bot_config",
  entity_id: "alpha",
  before_state: { version: 2, hash: "old" },
  after_state: { version: 3, hash: "new" },
  correlation_id: null,
  meta: {},
};

const eventNullStates = {
  ...eventBoth,
  id: 101,
  before_state: null,
  after_state: null,
};

describe("AuditExpandRow (T-419)", () => {
  it("renders before_state + after_state JSON pretty-print when both non-null", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({ events: [eventBoth], total: 1, limit: 50, offset: 0 });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit");
    await waitFor(() => {
      expect(screen.getByText("bot_config.apply")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("bot_config.apply"));
    await waitFor(() => {
      expect(screen.getByTestId("audit-expand-row")).toBeInTheDocument();
    });
    const beforePre = screen.getByTestId("before_state-pre");
    const afterPre = screen.getByTestId("after_state-pre");
    expect(beforePre.textContent).toContain('"version": 2');
    expect(beforePre.textContent).toContain('"hash": "old"');
    expect(afterPre.textContent).toContain('"version": 3');
    expect(afterPre.textContent).toContain('"hash": "new"');
  });

  it("renders null placeholders for before_state=null + after_state=null", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url.startsWith("/api/audit/?")) {
        return Promise.resolve({
          events: [eventNullStates],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }
      return Promise.reject(new Error(`unmocked: ${url}`));
    });
    mountAt("/audit");
    await waitFor(() => {
      expect(screen.getByText("bot_config.apply")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("bot_config.apply"));
    await waitFor(() => {
      expect(screen.getByTestId("before_state-null")).toHaveTextContent(
        /first version/,
      );
    });
    expect(screen.getByTestId("after_state-null")).toHaveTextContent(/entity removed/);
  });
});
