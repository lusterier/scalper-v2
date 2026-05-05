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

const sampleConfig = {
  id: 7,
  bot_id: "alpha",
  version: 3,
  applied_at: "2026-05-05T10:00:00Z",
  applied_by: "luster",
  config_yaml: "bot_id: alpha\n",
  config_hash: "deadbeef",
  notes: null,
};

function baseFetchImpl(url: string, options?: { method?: string }): Promise<unknown> {
  if (url === "/api/configs/alpha") return Promise.resolve(sampleConfig);
  if (url === "/api/configs/alpha/versions?limit=50")
    return Promise.resolve({ versions: [sampleConfig], total: 1, limit: 50, offset: 0 });
  if (url === "/api/configs/validate") {
    return Promise.resolve({
      valid: true,
      bot_id: "alpha",
      parsed_version: 3,
      errors: [],
    });
  }
  if (url === "/api/configs/alpha/apply" && options?.method === "POST") {
    return Promise.resolve({ ...sampleConfig, version: 4 });
  }
  return Promise.reject(new Error(`unmocked: ${url}`));
}

async function openApplyModal(): Promise<void> {
  await waitFor(
    () => {
      expect(screen.getByTestId("apply-button")).not.toBeDisabled();
    },
    { timeout: 3000 },
  );
  fireEvent.click(screen.getByTestId("apply-button"));
  await waitFor(() => {
    expect(screen.getByTestId("applied-by-input")).toBeInTheDocument();
  });
  fireEvent.change(screen.getByTestId("applied-by-input"), {
    target: { value: "luster" },
  });
}

describe("Strategy apply flow (T-416)", () => {
  it("apply happy POST 201 — modal closes + BOTH query keys invalidated (per WG#8)", async () => {
    mockFetch.mockImplementation(baseFetchImpl);
    mountAt("/strategy/alpha");
    await openApplyModal();
    fireEvent.submit(screen.getByTestId("apply-confirm").closest("form")!);
    await waitFor(() => {
      const applyCalls = mockFetch.mock.calls.filter(
        (c) =>
          c[0] === "/api/configs/alpha/apply" &&
          (c[1] as { method?: string } | undefined)?.method === "POST",
      );
      expect(applyCalls).toHaveLength(1);
    });
    // Per WG#8 — onSuccess MUST invalidate BOTH keys; refetch fires both
    // /api/configs/alpha and /api/configs/alpha/versions?... again.
    await waitFor(() => {
      const currentRefetches = mockFetch.mock.calls.filter(
        (c) => typeof c[0] === "string" && c[0] === "/api/configs/alpha",
      );
      const versionsRefetches = mockFetch.mock.calls.filter(
        (c) =>
          typeof c[0] === "string" &&
          (c[0] as string).startsWith("/api/configs/alpha/versions"),
      );
      // Each query fired ≥2× (initial + post-invalidation refetch).
      expect(currentRefetches.length).toBeGreaterThanOrEqual(2);
      expect(versionsRefetches.length).toBeGreaterThanOrEqual(2);
    });
    // Modal closes (applied-by input no longer in DOM after success).
    await waitFor(() => {
      expect(screen.queryByTestId("applied-by-input")).not.toBeInTheDocument();
    });
  });

  it("apply 422 error — error.message rendered inline in modal", async () => {
    const errorMessage =
      "API /api/configs/alpha/apply failed: 422 YAML validation failed: bad config";
    mockFetch.mockImplementation((url: string, options?: { method?: string }) => {
      if (url === "/api/configs/alpha/apply" && options?.method === "POST") {
        return Promise.reject(new Error(errorMessage));
      }
      return baseFetchImpl(url, options);
    });
    mountAt("/strategy/alpha");
    await openApplyModal();
    fireEvent.submit(screen.getByTestId("apply-confirm").closest("form")!);
    await waitFor(() => {
      expect(screen.getByTestId("apply-error")).toHaveTextContent(/422/);
    });
  });

  it("apply 409 bot_id mismatch — error.message rendered inline", async () => {
    const errorMessage =
      "API /api/configs/alpha/apply failed: 409 bot_id mismatch: URL='alpha' vs YAML body='beta'";
    mockFetch.mockImplementation((url: string, options?: { method?: string }) => {
      if (url === "/api/configs/alpha/apply" && options?.method === "POST") {
        return Promise.reject(new Error(errorMessage));
      }
      return baseFetchImpl(url, options);
    });
    mountAt("/strategy/alpha");
    await openApplyModal();
    fireEvent.submit(screen.getByTestId("apply-confirm").closest("form")!);
    await waitFor(() => {
      expect(screen.getByTestId("apply-error")).toHaveTextContent(/409/);
    });
  });

  it("submit-button-disabled while isPending — POST called exactly once on double-submit (per WG#1 / T-415 echo)", async () => {
    let resolveFetch: ((value: unknown) => void) | undefined;
    const slowApply = new Promise<unknown>((resolve) => {
      resolveFetch = resolve;
    });
    mockFetch.mockImplementation((url: string, options?: { method?: string }) => {
      if (url === "/api/configs/alpha/apply" && options?.method === "POST") {
        return slowApply;
      }
      return baseFetchImpl(url, options);
    });
    mountAt("/strategy/alpha");
    await openApplyModal();
    const confirm = screen.getByTestId("apply-confirm");
    const form = confirm.closest("form")!;
    fireEvent.submit(form);
    await waitFor(() => {
      expect(confirm).toBeDisabled();
    });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    fireEvent.submit(form);
    const applyCalls = mockFetch.mock.calls.filter(
      (c) =>
        c[0] === "/api/configs/alpha/apply" &&
        (c[1] as { method?: string } | undefined)?.method === "POST",
    );
    expect(applyCalls).toHaveLength(1);
    resolveFetch?.({ ...sampleConfig, version: 4 });
  });
});
