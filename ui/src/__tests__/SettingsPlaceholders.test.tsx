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
  mockFetch.mockImplementation((url: string) => {
    if (url === "/api/bots/") return Promise.resolve({ bots: [] });
    if (url === "/api/symbol-map/") return Promise.resolve({ entries: [] });
    return Promise.reject(new Error(`unmocked: ${url}`));
  });
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

describe("Settings placeholders (T-420)", () => {
  it("PluginRegistryPlaceholder renders 'Coming F4+' message", async () => {
    mountAt("/settings");
    await waitFor(() => {
      const card = screen.getByTestId("plugin-registry-placeholder");
      expect(card).toHaveTextContent(/Coming F4\+/);
      expect(card).toHaveTextContent(/\/api\/plugins\//);
    });
  });

  it("ApiKeyStatusPlaceholder renders 'H-022' + 'env-only' message + NO fetch from this section (per WG#2)", async () => {
    mountAt("/settings");
    await waitFor(() => {
      const card = screen.getByTestId("api-key-status-placeholder");
      expect(card).toHaveTextContent(/H-022/);
      expect(card).toHaveTextContent(/env-only/i);
      expect(card).toHaveTextContent(/BOT_<ID>_BYBIT_API_KEY/);
    });
    // Per WG#2 — H-022 zero-fetch guarantee. Verify NO fetch went to any
    // /api/keys or /api/api-keys URL.
    const keyFetches = mockFetch.mock.calls.filter(
      (c) =>
        typeof c[0] === "string" &&
        (((c[0] as string).startsWith("/api/keys")) ||
          ((c[0] as string).includes("api-key"))),
    );
    expect(keyFetches).toHaveLength(0);
  });
});
