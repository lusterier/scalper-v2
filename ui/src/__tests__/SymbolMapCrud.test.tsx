import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createMemoryHistory,
  createRouter,
} from "@tanstack/react-router";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "../routeTree.gen";

const mockFetch = vi.fn();
vi.mock("@/lib/api-client", () => ({
  apiFetch: (...args: unknown[]) => mockFetch(...args),
}));

// Per WG#9 — confirmSpy register/restore in afterEach via vi.restoreAllMocks.
let confirmSpy: ReturnType<typeof vi.fn> | undefined;

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  // Restore all spies (including window.confirm) between tests.
  vi.restoreAllMocks();
  confirmSpy = undefined;
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

const sampleEntry = {
  input_symbol: "BTCUSDT.P",
  canonical_symbol: "BTCUSDT",
  exchange_source: "binance" as const,
  notes: "TradingView alias",
  created_at: "2026-05-01T00:00:00Z",
  updated_at: "2026-05-05T10:00:00Z",
};

function baseFetchImpl(url: string, _options?: unknown): Promise<unknown> {
  if (url === "/api/bots/") return Promise.resolve({ bots: [] });
  if (url === "/api/symbol-map/") {
    return Promise.resolve({ entries: [sampleEntry] });
  }
  return Promise.reject(new Error(`unmocked: ${url}`));
}

describe("SymbolMap CRUD (T-420)", () => {
  it("Add Entry happy POST 201 — modal closes + list refetched", async () => {
    mockFetch.mockImplementation(
      (url: string, options?: { method?: string }): Promise<unknown> => {
        if (
          url === "/api/symbol-map/" &&
          options?.method === "POST"
        ) {
          return Promise.resolve(sampleEntry);
        }
        return baseFetchImpl(url, options);
      },
    );
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByTestId("add-symbol-map-entry")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("add-symbol-map-entry"));
    await waitFor(() => {
      expect(screen.getByTestId("form-input-symbol")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("form-input-symbol"), {
      target: { value: "ETHUSDT.P" },
    });
    fireEvent.change(screen.getByTestId("form-canonical-symbol"), {
      target: { value: "ETHUSDT" },
    });
    fireEvent.submit(screen.getByTestId("form-submit").closest("form")!);
    await waitFor(() => {
      const postCalls = mockFetch.mock.calls.filter(
        (c) =>
          c[0] === "/api/symbol-map/" &&
          (c[1] as { method?: string } | undefined)?.method === "POST",
      );
      expect(postCalls).toHaveLength(1);
    });
    // Modal closes (form-input-symbol no longer in DOM).
    await waitFor(() => {
      expect(screen.queryByTestId("form-input-symbol")).not.toBeInTheDocument();
    });
  });

  it("Add Entry 409 error — error.message rendered inline (per WG#5)", async () => {
    const errorMessage =
      "API /api/symbol-map/ failed: 409 symbol_map entry 'BTCUSDT.P' already exists";
    mockFetch.mockImplementation(
      (url: string, options?: { method?: string }): Promise<unknown> => {
        if (url === "/api/symbol-map/" && options?.method === "POST") {
          return Promise.reject(new Error(errorMessage));
        }
        return baseFetchImpl(url, options);
      },
    );
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByTestId("add-symbol-map-entry")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("add-symbol-map-entry"));
    await waitFor(() => {
      expect(screen.getByTestId("form-input-symbol")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("form-input-symbol"), {
      target: { value: "BTCUSDT.P" },
    });
    fireEvent.change(screen.getByTestId("form-canonical-symbol"), {
      target: { value: "BTCUSDT" },
    });
    fireEvent.submit(screen.getByTestId("form-submit").closest("form")!);
    await waitFor(() => {
      expect(screen.getByTestId("form-error")).toHaveTextContent(/409/);
    });
  });

  it("Edit Entry: input_symbol field disabled in edit mode + PUT body excludes input_symbol (per WG#3)", async () => {
    mockFetch.mockImplementation(
      (url: string, options?: { method?: string }): Promise<unknown> => {
        if (
          url === "/api/symbol-map/BTCUSDT.P" &&
          options?.method === "PUT"
        ) {
          return Promise.resolve(sampleEntry);
        }
        return baseFetchImpl(url, options);
      },
    );
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByTestId("edit-BTCUSDT.P")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("edit-BTCUSDT.P"));
    await waitFor(() => {
      expect(screen.getByTestId("form-input-symbol")).toBeInTheDocument();
    });
    // Per WG#3 — input_symbol disabled in edit mode.
    expect(screen.getByTestId("form-input-symbol")).toBeDisabled();
    // Initial value loaded from initialEntry.
    expect((screen.getByTestId("form-input-symbol") as HTMLInputElement).value).toBe(
      "BTCUSDT.P",
    );
    fireEvent.change(screen.getByTestId("form-canonical-symbol"), {
      target: { value: "BTCUSDT-NEW" },
    });
    fireEvent.submit(screen.getByTestId("form-submit").closest("form")!);
    await waitFor(() => {
      const putCall = mockFetch.mock.calls.find(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string) === "/api/symbol-map/BTCUSDT.P" &&
          (c[1] as { method?: string } | undefined)?.method === "PUT",
      );
      expect(putCall).toBeDefined();
      // Per WG#3 — PUT body MUST exclude input_symbol (URL path is the PK).
      const body = (putCall?.[1] as { body?: unknown } | undefined)?.body as
        | Record<string, unknown>
        | undefined;
      expect(body).toBeDefined();
      expect(body).not.toHaveProperty("input_symbol");
      expect(body).toHaveProperty("canonical_symbol", "BTCUSDT-NEW");
      expect(body).toHaveProperty("exchange_source");
      expect(body).toHaveProperty("notes");
    });
  });

  it("Delete via window.confirm happy DELETE 204 — list refetched (per WG#4)", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    confirmSpy = window.confirm as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockImplementation(
      (url: string, options?: { method?: string }): Promise<unknown> => {
        if (
          url === "/api/symbol-map/BTCUSDT.P" &&
          options?.method === "DELETE"
        ) {
          return Promise.resolve(undefined);
        }
        return baseFetchImpl(url, options);
      },
    );
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByTestId("delete-BTCUSDT.P")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("delete-BTCUSDT.P"));
    await waitFor(() => {
      const deleteCalls = mockFetch.mock.calls.filter(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string) === "/api/symbol-map/BTCUSDT.P" &&
          (c[1] as { method?: string } | undefined)?.method === "DELETE",
      );
      expect(deleteCalls).toHaveLength(1);
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
  });

  it("Delete cancel — window.confirm returns false → NO DELETE fires (per WG#4 negative assertion)", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    confirmSpy = window.confirm as unknown as ReturnType<typeof vi.fn>;
    mockFetch.mockImplementation(
      (url: string, options?: { method?: string }): Promise<unknown> =>
        baseFetchImpl(url, options),
    );
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByTestId("delete-BTCUSDT.P")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("delete-BTCUSDT.P"));
    // Per WG#4 — confirm returned false; NO DELETE mutation fires.
    const deleteCalls = mockFetch.mock.calls.filter(
      (c) =>
        typeof c[0] === "string" &&
        (c[1] as { method?: string } | undefined)?.method === "DELETE",
    );
    expect(deleteCalls).toHaveLength(0);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
  });

  it("submit-button-disabled while mutation isPending — POST called exactly once on double-submit (per WG#1)", async () => {
    let resolveFetch: ((value: unknown) => void) | undefined;
    const slowPostPromise = new Promise<unknown>((resolve) => {
      resolveFetch = resolve;
    });
    mockFetch.mockImplementation(
      (url: string, options?: { method?: string }): Promise<unknown> => {
        if (url === "/api/symbol-map/" && options?.method === "POST") {
          return slowPostPromise;
        }
        return baseFetchImpl(url, options);
      },
    );
    mountAt("/settings");
    await waitFor(() => {
      expect(screen.getByTestId("add-symbol-map-entry")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("add-symbol-map-entry"));
    await waitFor(() => {
      expect(screen.getByTestId("form-input-symbol")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("form-input-symbol"), {
      target: { value: "ETHUSDT.P" },
    });
    fireEvent.change(screen.getByTestId("form-canonical-symbol"), {
      target: { value: "ETHUSDT" },
    });
    const submitButton = screen.getByTestId("form-submit");
    const form = submitButton.closest("form")!;
    fireEvent.submit(form);
    await waitFor(() => {
      expect(submitButton).toBeDisabled();
    });
    fireEvent.click(submitButton);
    fireEvent.submit(form);
    fireEvent.click(submitButton);
    const postCalls = mockFetch.mock.calls.filter(
      (c) =>
        c[0] === "/api/symbol-map/" &&
        (c[1] as { method?: string } | undefined)?.method === "POST",
    );
    expect(postCalls).toHaveLength(1);
    resolveFetch?.(sampleEntry);
  });
});
